from __future__ import annotations

import json
import uuid
from argparse import Namespace
from pathlib import Path

import pytest

from smart_file_organizer.cli import (
    _hash_progress,
    _print_duplicates,
    _print_scan_summary,
    _progress_printer,
    _resolved_scan_inputs,
    main,
)
from smart_file_organizer.config import Config, write_default_config
from smart_file_organizer.history import ManifestStore
from smart_file_organizer.models import (
    DuplicateAnalysis,
    ManifestItem,
    OperationManifest,
    ScanIssue,
    ScanProgress,
)
from smart_file_organizer.organizer import apply_plan, build_organization_plan
from smart_file_organizer.scanner import scan_directory
from smart_file_organizer.utils import iso_timestamp


def _config_path(tmp_path: Path) -> Path:
    path = write_default_config(tmp_path / "settings.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["storage_dir"] = str(tmp_path / "state")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_output_survives_unencodable_console_filenames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filenames outside the console code page must not raise UnicodeEncodeError."""
    import io

    root = tmp_path / "unicode"
    root.mkdir()
    (root / "café résumé 📄.txt").write_text("unicode filename\n", encoding="utf-8")
    config = _config_path(tmp_path)

    class AsciiStream(io.TextIOWrapper):
        def __init__(self, buffer: io.BytesIO) -> None:  # type: ignore[override]
            super().__init__(buffer, encoding="ascii", errors="strict", newline="")

    out = AsciiStream(io.BytesIO())
    err = AsciiStream(io.BytesIO())
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    try:
        # ascii/strict would raise if the CLI printed the raw emoji.
        assert main(["preview", str(root), "--config", str(config)]) == 0
        assert main(["organize", str(root), "--apply", "--config", str(config)]) == 0
    finally:
        out.flush()
        err.flush()
        stdout = out.buffer.getvalue().decode("ascii", errors="replace")
        out.detach()
        err.detach()
    assert "Operation ID:" in stdout
    assert "Operation status: completed" in stdout


def test_progress_helpers_emit_only_at_configured_intervals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _progress_printer("Scanning", every=2)(ScanProgress(1, 1, "one"))
    _progress_printer("Scanning", every=2)(ScanProgress(2, 3, "two"))
    _hash_progress("Hashing", every=2)(1, 3, "one")
    _hash_progress("Hashing", every=2)(2, 3, "two")
    output = capsys.readouterr()
    assert "Scanning: 2 files, 3 directories" in output.err
    assert "Hashing: 2/3 files" in output.err


def test_scan_input_resolution_with_no_config_file_or_separate_destination(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    args = Namespace(
        path=str(root),
        destination=str(root),
        report=None,
        exclude=None,
        recursive=True,
        include_hidden=None,
    )
    actual_root, destination, excluded = _resolved_scan_inputs(
        args, Config(config_file=None), tmp_path / "state"
    )
    assert actual_root == destination == root.resolve()
    assert excluded == [tmp_path / "state"]


def test_summary_helpers_print_issue_limit_and_empty_duplicate_analysis(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan = scan_directory(sample_tree)
    scan.issues = [ScanIssue(f"path-{i}", "read", "warning") for i in range(6)]
    _print_scan_summary(scan)
    output = capsys.readouterr().out
    assert "… and 1 more" in output
    analysis = DuplicateAnalysis(
        groups=[],
        issues=[ScanIssue("bad.bin", "hash", "unreadable")],
        files_hashed=1,
        bytes_hashed=10,
        duration_seconds=0.0,
    )
    _print_duplicates(analysis)
    duplicate_output = capsys.readouterr().out
    assert "No exact duplicate contents" in duplicate_output
    assert "Hashing warnings: 1" in duplicate_output
    assert "unreadable" in duplicate_output


def test_cli_empty_scan_preview_and_duplicate_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_path(tmp_path)
    root = tmp_path / "empty"
    root.mkdir()
    assert main(["scan", str(root), "--config", str(config)]) == 0
    assert "(no files found)" in capsys.readouterr().out
    assert main(["preview", str(root), "--config", str(config)]) == 0
    assert "Nothing needs organizing" in capsys.readouterr().out
    assert main(["duplicates", str(root), "--config", str(config)]) == 0
    assert "No exact duplicate contents" in capsys.readouterr().out


def test_cli_exclude_applies_glob_patterns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_path(tmp_path)
    root = tmp_path / "files"
    (root / "cache").mkdir(parents=True)
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    (root / "cache" / "ignore.log").write_text("ignore", encoding="utf-8")
    assert main(["scan", str(root), "--config", str(config), "--exclude", "cache/*"]) == 0
    output = capsys.readouterr().out
    assert "Files: 1" in output
    assert "excluded 1" in output


def test_cli_history_empty_and_corrupt_history_reporting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_path(tmp_path)
    assert main(["history", "--config", str(config)]) == 0
    assert "No operation history" in capsys.readouterr().out
    store = ManifestStore(tmp_path / "state")
    store.manifest_dir.mkdir(parents=True)
    (store.manifest_dir / f"{'f' * 32}.json").write_text("broken", encoding="utf-8")
    assert main(["history", "--config", str(config)]) == 1
    assert "History warning" in capsys.readouterr().err


def test_cli_recover_and_report_stdout(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_path(tmp_path)
    scan = scan_directory(sample_tree)
    plan = build_organization_plan(scan, sample_tree / "Organized")
    store = ManifestStore(tmp_path / "state")
    manifest = apply_plan(plan, store, chunk_size=4096)
    manifest.items[0].status = "moving"
    manifest.status = "running"
    store.save(manifest)
    assert main(["recover", manifest.run_id, "--config", str(config)]) == 0
    assert "Recovery does not delete" in capsys.readouterr().out
    assert main(["report", manifest.run_id, "--config", str(config)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["operation_id"] == manifest.run_id
    assert report["duration_seconds"] >= 0


def test_cli_recover_prints_interrupted_note_and_partial_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_path(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("preserve", encoding="utf-8")
    destination_root = root / "Organized"
    now = iso_timestamp()
    run_id = uuid.uuid4().hex
    item = ManifestItem(
        source=str(source),
        destination=str(destination_root / "source.txt"),
        category="Documents",
        size=8,
        original_snapshot={},
        expected_sha256="0" * 64,
        status="partial",
        error="simulated partial copy",
    )
    manifest = OperationManifest(
        run_id=run_id,
        kind="organize",
        created_at=now,
        updated_at=now,
        status="partial",
        root=str(root),
        destination_root=str(destination_root),
        items=[item],
    )
    store = ManifestStore(tmp_path / "state")
    store.save(manifest)
    assert main(["recover", run_id, "--config", str(config)]) == 1
    output = capsys.readouterr().out
    assert "partial/" in output
    assert "simulated partial copy" in output

    manifest.status = "completed"
    item.status = "moved"
    item.undo_status = "needs_attention"
    item.undo_error = "both paths require manual review"
    store.save(manifest)
    assert main(["recover", run_id, "--config", str(config)]) == 1
    attention_output = capsys.readouterr().out
    assert "Undo: both paths require manual review" in attention_output


def test_cli_undo_reports_skipped_error_and_returns_nonzero(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_path(tmp_path)
    store = ManifestStore(tmp_path / "state")
    plan = build_organization_plan(scan_directory(sample_tree), sample_tree / "Organized")
    manifest = apply_plan(plan, store, chunk_size=4096)
    Path(manifest.items[0].destination).write_text("user edit", encoding="utf-8")
    assert main(["undo", manifest.run_id, "--config", str(config)]) == 1
    output = capsys.readouterr().out
    assert "skipped:" in output
    assert "metadata changed" in output or "contents changed" in output


def test_cli_invalid_keep_and_gui_success_and_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import smart_file_organizer.cli as cli_module
    import smart_file_organizer.gui as gui_module

    config = _config_path(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_text("x", encoding="utf-8")
    assert (
        main(["duplicates", str(root), "--config", str(config), "--keep", str(root / "file.txt")])
        == 2
    )
    assert "only valid with --quarantine" in capsys.readouterr().err

    launched: list[object] = []
    monkeypatch.setattr(gui_module, "launch_gui", lambda value: launched.append(value))
    assert main(["gui", "--config", str(config)]) == 0
    assert launched
    monkeypatch.setattr(
        gui_module,
        "launch_gui",
        lambda _value: (_ for _ in ()).throw(RuntimeError("no display")),
    )
    assert main(["gui", "--config", str(config)]) == 1
    assert "GUI could not be started" in capsys.readouterr().err

    monkeypatch.setattr(
        cli_module,
        "scan_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert main(["scan", str(root), "--config", str(config)]) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_cli_returns_nonzero_for_recoverable_scan_issue(
    sample_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.cli as cli_module

    config = _config_path(tmp_path)
    scan = scan_directory(sample_tree)
    scan.issues.append(ScanIssue(str(sample_tree), "scan_directory", "simulated warning"))
    monkeypatch.setattr(cli_module, "scan_directory", lambda *_args, **_kwargs: scan)
    assert main(["scan", str(sample_tree), "--config", str(config)]) == 1


def test_cli_config_init_default_destination_and_demo_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import smart_file_organizer.config as config_module

    default_path = tmp_path / "user-config.json"
    monkeypatch.setattr(config_module, "default_config_path", lambda: default_path)
    assert main(["config", "init"]) == 0
    assert json.loads(default_path.read_text(encoding="utf-8"))["version"] == 1
    output = capsys.readouterr().out
    assert str(default_path) in output

    demo_root = tmp_path / "synthetic-demo"
    assert main(["demo", str(demo_root)]) == 0
    assert (demo_root / "notes.txt").read_bytes() == (demo_root / "notes-copy.txt").read_bytes()
    assert "Created synthetic demo files" in capsys.readouterr().out


def test_cli_config_init_can_repair_invalid_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken-config.json"
    path.write_text("bad", encoding="utf-8")
    assert main(["config", "init", "--config", str(path), "--force"]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    capsys.readouterr()


def test_cli_oserror_returns_filesystem_error_code(
    sample_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import smart_file_organizer.cli as cli_module

    config = _config_path(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "scan_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("synthetic denial")),
    )
    assert main(["scan", str(sample_tree), "--config", str(config)]) == 1
    assert "Filesystem error: synthetic denial" in capsys.readouterr().err
