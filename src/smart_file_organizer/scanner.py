"""Non-mutating recursive scanning with explicit symlink and error handling."""

from __future__ import annotations

import fnmatch
import os
import stat
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .classifier import classify_file
from .config import Config
from .models import FileRecord, FileSnapshot, ScanIssue, ScanProgress, ScanResult
from .utils import FileSafetyError, is_relative_to, iso_timestamp, read_file_header

ProgressCallback = Callable[[ScanProgress], None]


def _is_hidden(name: str, stat_result: os.stat_result | None = None) -> bool:
    if name.startswith("."):
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0) if stat_result else 0
    hidden_flag = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    return bool(attributes & hidden_flag)


def _matches_exclusion(relative: str, name: str, patterns: Iterable[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in patterns
    )


def scan_directory(
    root: str | Path,
    *,
    recursive: bool = True,
    include_hidden: bool | None = None,
    config: Config | None = None,
    excluded_dirs: Iterable[str] | None = None,
    excluded_patterns: Iterable[str] | None = None,
    excluded_paths: Iterable[str | Path] = (),
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Scan regular files without following symlinks or changing filesystem state.

    Symlinks and special files are skipped. Permission and I/O failures are recorded
    as recoverable issues so one inaccessible folder does not abort the whole scan.
    """
    started_clock = time.monotonic()
    started_at = iso_timestamp()
    selected = Path(root).expanduser()
    try:
        resolved_root = selected.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(f"Cannot access scan directory {selected}: {exc}") from exc
    if not resolved_root.is_dir():
        raise NotADirectoryError(f"Scan path is not a directory: {resolved_root}")

    config = config or Config()
    include_hidden = config.include_hidden if include_hidden is None else include_hidden
    excluded_names = tuple(config.excluded_dirs if excluded_dirs is None else excluded_dirs)
    patterns = tuple(config.excluded_patterns if excluded_patterns is None else excluded_patterns)
    excluded_roots: list[Path] = []
    for path in excluded_paths:
        try:
            excluded_roots.append(Path(path).expanduser().resolve(strict=False))
        except (OSError, RuntimeError):
            continue

    files: list[FileRecord] = []
    issues: list[ScanIssue] = []
    files_seen = 0
    directories_seen = 0
    hidden_skipped = 0
    excluded_skipped = 0
    symlinks_skipped = 0
    special_files_skipped = 0
    stack: list[Path] = [resolved_root]

    def is_excluded_path(candidate: Path) -> bool:
        return any(is_relative_to(candidate, excluded) for excluded in excluded_roots)

    while stack:
        directory = stack.pop()
        if is_excluded_path(directory):
            excluded_skipped += 1
            continue
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: (entry.name.casefold(), entry.name))
        except OSError as exc:
            issues.append(ScanIssue(str(directory), "scan_directory", str(exc)))
            continue
        directories_seen += 1
        child_directories: list[Path] = []
        for entry in entries:
            candidate = Path(entry.path)
            try:
                if entry.is_symlink():
                    symlinks_skipped += 1
                    continue
                if os.name == "nt":
                    # os.scandir on Windows leaves device, inode, and link count as
                    # zero, which would defeat hard-link aware duplicate accounting.
                    # Entries here are already known not to be symbolic links.
                    info = os.lstat(entry.path)
                else:
                    info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                issues.append(ScanIssue(str(candidate), "stat", str(exc)))
                continue

            try:
                relative = candidate.relative_to(resolved_root).as_posix()
            except ValueError:
                issues.append(
                    ScanIssue(
                        str(candidate),
                        "validate_path",
                        "Entry resolved outside selected root; skipped.",
                    )
                )
                continue

            if entry.name in excluded_names or _matches_exclusion(relative, entry.name, patterns):
                excluded_skipped += 1
                continue
            if is_excluded_path(candidate):
                excluded_skipped += 1
                continue
            if not include_hidden and _is_hidden(entry.name, info):
                hidden_skipped += 1
                continue

            if stat.S_ISDIR(info.st_mode):
                if recursive:
                    child_directories.append(candidate)
                continue
            if not stat.S_ISREG(info.st_mode):
                special_files_skipped += 1
                continue

            snapshot = FileSnapshot.from_stat(info)
            classification_warning: str | None = None
            header: bytes | None = None
            if config.signature_validation:
                try:
                    header = read_file_header(candidate)
                except (OSError, FileSafetyError) as exc:
                    classification_warning = f"Could not validate signature: {exc}"
                    issues.append(ScanIssue(str(candidate), "read_signature", str(exc)))
            classification = classify_file(candidate, config=config, header=header)
            if classification.warning:
                classification_warning = classification.warning
            files.append(
                FileRecord(
                    path=candidate,
                    relative_path=relative,
                    snapshot=snapshot,
                    extension=classification.extension,
                    category=classification.category,
                    signature=classification.signature,
                    warning=classification_warning,
                )
            )
            files_seen += 1
            if progress is not None:
                progress(ScanProgress(files_seen, directories_seen, str(candidate)))

        # Reverse sorting gives a stable ascending traversal when popping the stack.
        stack.extend(reversed(child_directories))

    files.sort(key=lambda record: (record.relative_path.casefold(), record.relative_path))
    finished_at = iso_timestamp()
    return ScanResult(
        root=resolved_root,
        files=files,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=time.monotonic() - started_clock,
        directories_scanned=directories_seen,
        hidden_skipped=hidden_skipped,
        excluded_skipped=excluded_skipped,
        symlinks_skipped=symlinks_skipped,
        special_files_skipped=special_files_skipped,
        issues=issues,
    )
