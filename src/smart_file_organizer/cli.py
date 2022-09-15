"""Command-line interface for Smart File Organizer & Duplicate Detector."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, ConfigError, load_config, resolve_storage_dir, write_default_config
from .demo import create_demo_data
from .duplicates import find_duplicates
from .history import ManifestError, ManifestStore, recover_operation, undo_operation
from .models import DuplicateAnalysis, ScanProgress
from .organizer import (
    apply_plan,
    build_organization_plan,
    build_quarantine_plan,
    default_destination,
)
from .reports import (
    operation_report_data,
    plan_report_data,
    render_report,
    scan_report_data,
    write_report,
)
from .scanner import scan_directory
from .utils import FileSafetyError, format_bytes

_LOG = logging.getLogger("smart_file_organizer")


def _reconfigure_std_streams() -> None:
    """Keep console output alive when filenames contain non-codepage characters.

    Windows consoles often use a legacy code page (for example cp1252) that cannot
    encode every Unicode filename. Reconfiguring the error handler turns those
    characters into readable escapes instead of raising UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Path to a JSON configuration file (default: the per-user config location).",
        default=argparse.SUPPRESS,
    )


def _add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="Directory to scan; files are never changed by scanning.")
    recursive = parser.add_mutually_exclusive_group()
    recursive.add_argument(
        "--recursive", dest="recursive", action="store_true", help="Scan nested folders (default)."
    )
    recursive.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Scan only the selected directory.",
    )
    parser.set_defaults(recursive=True)
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        default=None,
        help="Include hidden files and folders.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Additional folder name or glob to exclude; repeat as needed.",
    )


def _add_organization_arguments(parser: argparse.ArgumentParser) -> None:
    _add_scan_arguments(parser)
    parser.add_argument("--destination", help="Destination directory (default: PATH/Organized).")
    parser.add_argument("--by-month", action="store_true", help="Add modified year/month folders.")
    parser.add_argument(
        "--by-extension", action="store_true", help="Add an extension folder below the category."
    )
    parser.add_argument("--report", help="Write a JSON, CSV, or HTML report to this path.")
    parser.add_argument(
        "--format",
        choices=("json", "csv", "html"),
        default="json",
        help="Report format when --report is used.",
    )


def _add_config_to_all(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # Also accept --config after the subcommand without overriding a global value.
    for subparser in subparsers.choices.values():
        _add_config_argument(subparser)


def build_parser() -> argparse.ArgumentParser:
    """Create the argparse command tree."""
    parser = argparse.ArgumentParser(
        prog="smart-organizer",
        description=(
            "Scan, preview, safely organize, and find exact duplicate files. "
            "Organization and quarantine are previews by default; applying them requires --apply. "
            "Undo is an explicit, separate command."
        ),
        epilog="Use 'smart-organizer demo' to generate a safe synthetic dataset for practice.",
    )
    parser.add_argument("--version", action="version", version=f"smart-organizer {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Show diagnostic logging on stderr.")
    _add_config_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    scan_parser = commands.add_parser("scan", help="Scan a folder and summarize its files.")
    _add_scan_arguments(scan_parser)
    scan_parser.add_argument("--report", help="Write a JSON, CSV, or HTML scan report.")
    scan_parser.add_argument("--format", choices=("json", "csv", "html"), default="json")

    preview_parser = commands.add_parser(
        "preview", help="Preview a safe organization plan (dry-run)."
    )
    _add_organization_arguments(preview_parser)

    organize_parser = commands.add_parser(
        "organize", help="Preview by default; move files only when --apply is supplied."
    )
    _add_organization_arguments(organize_parser)
    organize_parser.add_argument(
        "--apply", action="store_true", help="Apply the previewed moves; never overwrites files."
    )

    duplicates_parser = commands.add_parser(
        "duplicates", help="Find exact duplicate files by size and SHA-256."
    )
    _add_scan_arguments(duplicates_parser)
    duplicates_parser.add_argument(
        "--report", help="Write a JSON, CSV, or HTML duplicate-analysis report."
    )
    duplicates_parser.add_argument("--format", choices=("json", "csv", "html"), default="json")
    duplicates_parser.add_argument(
        "--quarantine",
        action="store_true",
        help="Preview reversible quarantine of copies, keeping one per group.",
    )
    duplicates_parser.add_argument(
        "--apply", action="store_true", help="Apply quarantine only when used with --quarantine."
    )
    duplicates_parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="PATH",
        help="Retain this file in its duplicate group; repeat for multiple groups.",
    )

    commands.add_parser("history", help="List persisted operations.")

    undo_parser = commands.add_parser(
        "undo", help="Safely undo a completed organization or quarantine operation."
    )
    undo_parser.add_argument("run_id", help="Operation ID printed by the apply command.")

    recover_parser = commands.add_parser(
        "recover", help="Inspect and reconcile a possibly interrupted operation."
    )
    recover_parser.add_argument("run_id", help="Operation ID printed by the apply command.")

    report_parser = commands.add_parser("report", help="Export a persisted operation report.")
    report_parser.add_argument("run_id", help="Operation ID printed by the apply command.")
    report_parser.add_argument("--format", choices=("json", "csv", "html"), default="json")
    report_parser.add_argument("--output", help="Output file; omit to print to stdout.")

    commands.add_parser("gui", help="Launch the optional Tkinter desktop interface.")

    demo_parser = commands.add_parser(
        "demo", help="Create synthetic files for a safe practice run."
    )
    demo_parser.add_argument(
        "directory",
        nargs="?",
        default="smart-organizer-demo",
        help="New or empty directory to populate.",
    )

    config_parser = commands.add_parser("config", help="Show or initialize user configuration.")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    show_parser = config_commands.add_parser("show", help="Print effective configuration as JSON.")
    _add_config_argument(show_parser)
    init_parser = config_commands.add_parser("init", help="Write a starter JSON configuration.")
    _add_config_argument(init_parser)
    init_parser.add_argument(
        "path", nargs="?", help="Output path (default: the per-user config path)."
    )
    init_parser.add_argument(
        "--force", action="store_true", help="Replace the file at the selected config path."
    )

    _add_config_to_all(commands)
    # config subcommands inherit --config from config_parser; init's `path` is the output path.
    return parser


def _progress_printer(message: str, *, every: int = 250):
    def callback(progress: ScanProgress) -> None:
        if progress.files_seen and progress.files_seen % every == 0:
            print(
                f"{message}: {progress.files_seen} files, {progress.directories_seen} directories…",
                file=sys.stderr,
            )

    return callback


def _hash_progress(label: str, every: int = 100):
    def callback(done: int, total: int, path: str) -> None:
        if total and (done == total or done % every == 0):
            print(f"{label}: {done}/{total} files", file=sys.stderr)

    return callback


def _resolved_scan_inputs(
    args: argparse.Namespace, config: Any, storage_dir: Path
) -> tuple[Path, Path, list[Path]]:
    root = Path(args.path).expanduser().resolve(strict=False)
    destination_arg = getattr(args, "destination", None)
    destination = (
        Path(destination_arg).expanduser().resolve(strict=False)
        if destination_arg
        else default_destination(root, config)
    )
    excluded: list[Path] = [storage_dir]
    if config.config_file is not None:
        excluded.append(config.config_file)
    # Exclude the configured managed destination even on read-only scans to avoid rescanning it.
    if destination != root:
        excluded.append(destination)
    return root, destination, excluded


def _scan_from_args(args: argparse.Namespace, config: Any, storage_dir: Path):
    root, destination, excluded_paths = _resolved_scan_inputs(args, config, storage_dir)
    if getattr(args, "report", None):
        excluded_paths.append(Path(args.report).expanduser().resolve(strict=False))
    extra_excludes = tuple(getattr(args, "exclude", None) or ())
    return scan_directory(
        root,
        recursive=args.recursive,
        include_hidden=getattr(args, "include_hidden", None),
        config=config,
        excluded_dirs=tuple(config.excluded_dirs),
        excluded_patterns=tuple(config.excluded_patterns) + extra_excludes,
        excluded_paths=excluded_paths,
        progress=_progress_printer("Scanning"),
    ), destination


def _category_summary(scan: Any) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for file_record in scan.files:
        counts = result.setdefault(file_record.category, [0, 0])
        counts[0] += 1
        counts[1] += file_record.size
    return result


def _print_scan_summary(scan: Any) -> None:
    print(f"Directory: {scan.root}")
    print(
        f"Files: {len(scan.files)}   Directories: {scan.directories_scanned}   Total: {format_bytes(scan.total_bytes)}"
    )
    print("Categories:")
    categories = _category_summary(scan)
    if not categories:
        print("  (no files found)")
    for category in sorted(categories, key=str.casefold):
        count, byte_count = categories[category]
        print(f"  {category:<18} {count:>6} files   {format_bytes(byte_count):>10}")
    skips = (
        f"hidden {scan.hidden_skipped}, excluded {scan.excluded_skipped}, "
        f"symlinks {scan.symlinks_skipped}, special {scan.special_files_skipped}"
    )
    print(f"Skipped: {skips}")
    if scan.issues:
        print(f"Warnings: {len(scan.issues)} recoverable scan issue(s).")
        for issue in scan.issues[:5]:
            print(f"  [{issue.operation}] {issue.path}: {issue.message}")
        if len(scan.issues) > 5:
            print(f"  … and {len(scan.issues) - 5} more; see the exported report.")


def _print_plan(plan: Any) -> None:
    print(f"Preview generated: {plan.generated_at}")
    print(
        f"Selected files: {len(plan.items)} move(s); {plan.skipped_count} skipped/unchanged; {plan.conflict_count} name conflict(s) resolved."
    )
    print("DRY RUN: no selected files have been moved.")
    if plan.items:
        print("\nSOURCE  ->  DESTINATION")
        for item in plan.items:
            print(f"{item.source}  ->  {item.destination}")
    else:
        print("Nothing needs organizing.")
    if plan.warnings:
        print("\nWarnings:")
        for warning in plan.warnings:
            print(f"  - {warning}")


def _print_duplicates(analysis: DuplicateAnalysis) -> None:
    print(f"Duplicate groups: {len(analysis.groups)}")
    print(f"Files in duplicate groups: {analysis.duplicate_file_count}")
    print(
        "Estimated physically reclaimable storage if extra copies were removed: "
        f"{format_bytes(analysis.reclaimable_bytes)} (nothing was deleted or moved)."
    )
    if not analysis.groups:
        print("No exact duplicate contents found.")
    for index, group in enumerate(analysis.groups, start=1):
        print(
            f"\nGroup {index}: {len(group.files)} files, each {format_bytes(group.size)}, "
            f"SHA-256 {group.sha256[:16]}…"
        )
        print(f"  Keep by default: {group.default_keep}")
        for duplicate in group.files:
            print(f"  {duplicate.path}")
    if analysis.issues:
        print(f"\nHashing warnings: {len(analysis.issues)}")
        for issue in analysis.issues[:5]:
            print(f"  {issue.path}: {issue.message}")


def _maybe_write_report(data: dict[str, Any], args: argparse.Namespace) -> None:
    output = getattr(args, "report", None)
    if output:
        written = write_report(data, output, getattr(args, "format", None))
        print(f"Report written: {written}")


def _write_report_to_output(data: dict[str, Any], output: str | None, format_name: str) -> None:
    if output:
        destination = write_report(data, output, format_name)
        print(f"Report written: {destination}")
    else:
        print(render_report(data, format_name), end="")


def _handle_scan(args: argparse.Namespace, config: Any, storage_dir: Path) -> int:
    scan, _ = _scan_from_args(args, config, storage_dir)
    _print_scan_summary(scan)
    _maybe_write_report(scan_report_data(scan), args)
    return 1 if scan.issues else 0


def _handle_organize(args: argparse.Namespace, config: Any, storage_dir: Path) -> int:
    scan, destination = _scan_from_args(args, config, storage_dir)
    _print_scan_summary(scan)
    plan = build_organization_plan(
        scan,
        destination,
        config=config,
        by_month=args.by_month,
        by_extension=args.by_extension,
        protected_paths=(storage_dir,),
        progress=_hash_progress("Fingerprinting"),
    )
    _print_plan(plan)
    if not getattr(args, "apply", False):
        print(
            "\nTo apply this exact kind of operation, rerun with --apply after reviewing the preview."
        )
        _maybe_write_report(plan_report_data(scan, plan), args)
        return 1 if scan.issues else 0

    store = ManifestStore(storage_dir)
    manifest = apply_plan(
        plan,
        store,
        kind="organize",
        chunk_size=config.hash_chunk_size,
        progress=lambda done, total, path, status: print(
            f"[{done}/{total}] {status}: {path}", file=sys.stderr
        ),
    )
    manifest_path = store.manifest_path(manifest.run_id)
    print(f"\nOperation ID: {manifest.run_id}")
    print(f"Manifest: {manifest_path}")
    print(f"Operation status: {manifest.status}")
    _maybe_write_report(operation_report_data(manifest, manifest_path=str(manifest_path)), args)
    return 1 if manifest.status in {"failed", "partial"} or scan.issues else 0


def _handle_duplicates(args: argparse.Namespace, config: Any, storage_dir: Path) -> int:
    if args.apply and not args.quarantine:
        raise ValueError("--apply is valid for duplicates only when used with --quarantine")
    if args.keep and not args.quarantine:
        raise ValueError("--keep is only valid with --quarantine")
    scan, _ = _scan_from_args(args, config, storage_dir)
    analysis = find_duplicates(
        scan.files,
        config=config,
        progress=lambda done, total, path: (
            print(f"Hashing duplicate candidates: {done}/{total}", file=sys.stderr)
            if total and (done == total or done % 100 == 0)
            else None
        ),
    )
    _print_scan_summary(scan)
    _print_duplicates(analysis)
    _maybe_write_report(scan_report_data(scan, analysis), args)
    if not args.quarantine:
        return 1 if scan.issues or analysis.issues else 0

    operation_id = uuid.uuid4().hex
    quarantine_root = storage_dir / "quarantine" / operation_id
    plan, selections = build_quarantine_plan(
        scan,
        analysis,
        quarantine_root,
        keep_paths=args.keep,
    )
    print(f"\nQuarantine preview: {len(plan.items)} file(s) would be moved to {quarantine_root}.")
    for item in plan.items:
        print(f"  {item.source}  ->  {item.destination}")
    if not args.apply:
        print("DRY RUN: quarantine is optional and reversible; no files were moved.")
        print("Add both --quarantine and --apply to execute this explicit operation.")
        return 1 if scan.issues or analysis.issues else 0
    metadata = {
        "duplicate_group_count": len(analysis.groups),
        "duplicate_file_count": analysis.duplicate_file_count,
        "estimated_reclaimable_bytes": analysis.reclaimable_bytes,
        "retained_paths": {digest: str(path) for digest, path in selections.items()},
        "quarantine_is_reversible": True,
        "permanent_deletion": False,
    }
    store = ManifestStore(storage_dir)
    manifest = apply_plan(
        plan,
        store,
        kind="quarantine",
        run_id=operation_id,
        metadata=metadata,
        chunk_size=config.hash_chunk_size,
    )
    manifest_path = store.manifest_path(manifest.run_id)
    print(f"Operation ID: {manifest.run_id}")
    print(f"Manifest: {manifest_path}")
    print(f"Status: {manifest.status}; quarantine does not delete files.")
    return 1 if manifest.status in {"failed", "partial"} or scan.issues or analysis.issues else 0


def _handle_history(storage_dir: Path) -> int:
    store = ManifestStore(storage_dir)
    manifests, errors = store.list_manifests()
    if not manifests and not errors:
        print("No operation history yet.")
        return 0
    print(
        "RUN ID                            KIND         STATUS             CREATED                MOVED/PLANNED"
    )
    for manifest in manifests:
        moved = sum(item.status == "moved" for item in manifest.items)
        print(
            f"{manifest.run_id}  {manifest.kind:<12} {manifest.status:<18} "
            f"{manifest.created_at:<22} {moved}/{len(manifest.items)}"
        )
    for error in errors:
        print(f"History warning: {error}", file=sys.stderr)
    return 1 if errors else 0


def _handle_undo(args: argparse.Namespace, storage_dir: Path, config: Any) -> int:
    store = ManifestStore(storage_dir)
    manifest = undo_operation(store, args.run_id, chunk_size=config.hash_chunk_size)
    undone = sum(item.undo_status == "undone" for item in manifest.items)
    attention = sum(
        item.undo_status in {"failed", "skipped", "needs_attention"} for item in manifest.items
    )
    print(f"Operation ID: {manifest.run_id}")
    print(f"Undo status: {manifest.status}; restored {undone}/{len(manifest.items)} files.")
    for item in manifest.items:
        if item.undo_error:
            print(f"  {item.undo_status}: {item.source}: {item.undo_error}")
    print(f"Manifest: {store.manifest_path(manifest.run_id)}")
    return 1 if attention else 0


def _handle_recover(args: argparse.Namespace, storage_dir: Path) -> int:
    store = ManifestStore(storage_dir)
    manifest = recover_operation(store, args.run_id)
    print(f"Operation ID: {manifest.run_id}")
    print(f"Status: {manifest.status}")
    needs_attention = manifest.status in {"failed", "partial", "interrupted"}
    for item in manifest.items:
        item_needs_attention = item.status in {
            "failed",
            "partial",
            "interrupted",
        } or item.undo_status in {
            "failed",
            "needs_attention",
            "pending",
        }
        if item_needs_attention:
            needs_attention = True
            print(f"  {item.status}/{item.undo_status}: {item.source} -> {item.destination}")
            if item.recovery_note:
                print(f"    {item.recovery_note}")
            if item.error:
                print(f"    {item.error}")
            if item.undo_error:
                print(f"    Undo: {item.undo_error}")
    print("Recovery does not delete, overwrite, or guess about ambiguous files.")
    return 1 if needs_attention else 0


def _handle_report(args: argparse.Namespace, storage_dir: Path) -> int:
    store = ManifestStore(storage_dir)
    manifest = store.load(args.run_id)
    data = operation_report_data(manifest, manifest_path=str(store.manifest_path(manifest.run_id)))
    _write_report_to_output(data, args.output, args.format)
    return 0


def _handle_gui(config: Any) -> int:
    from .gui import launch_gui

    try:
        launch_gui(config)
    except (ImportError, RuntimeError) as exc:
        print(f"GUI could not be started: {exc}", file=sys.stderr)
        return 1
    return 0


def _dispatch(args: argparse.Namespace, config: Any, storage_dir: Path) -> int:
    if args.command == "scan":
        return _handle_scan(args, config, storage_dir)
    if args.command in {"preview", "organize"}:
        return _handle_organize(args, config, storage_dir)
    if args.command == "duplicates":
        return _handle_duplicates(args, config, storage_dir)
    if args.command == "history":
        return _handle_history(storage_dir)
    if args.command == "undo":
        return _handle_undo(args, storage_dir, config)
    if args.command == "recover":
        return _handle_recover(args, storage_dir)
    if args.command == "report":
        return _handle_report(args, storage_dir)
    if args.command == "gui":
        return _handle_gui(config)
    if args.command == "demo":
        path = create_demo_data(args.directory)
        print(f"Created synthetic demo files: {path}")
        print(f'Try: smart-organizer scan "{path}" --recursive')
        print(f'Then preview (safe): smart-organizer preview "{path}"')
        return 0
    if args.command == "config":
        if args.config_command == "show":
            print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
            return 0
        if args.config_command == "init":
            destination = args.path or getattr(args, "config_path", None)
            if destination is None:
                from .config import default_config_path

                destination = default_config_path()
            path = write_default_config(destination, overwrite=args.force)
            print(f"Configuration written: {path}")
            return 0
    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    _reconfigure_std_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    try:
        if args.command == "config" and args.config_command == "init":
            # Initialization must be able to repair a missing or invalid config file.
            config = Config()
        else:
            config = load_config(getattr(args, "config_path", None))
        storage_dir = resolve_storage_dir(config)
        return _dispatch(args, config, storage_dir)
    except KeyboardInterrupt:
        print(
            "Interrupted. Completed per-file moves are recorded in the operation manifest.",
            file=sys.stderr,
        )
        return 130
    except (
        ConfigError,
        ManifestError,
        FileSafetyError,
        FileNotFoundError,
        FileExistsError,
        NotADirectoryError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _LOG.debug("Command failed", exc_info=True)
        return 2
    except OSError as exc:
        print(f"Filesystem error: {exc}", file=sys.stderr)
        _LOG.debug("Filesystem operation failed", exc_info=True)
        return 1


if __name__ == "__main__":  # pragma: no cover - used for direct module execution
    raise SystemExit(main())
