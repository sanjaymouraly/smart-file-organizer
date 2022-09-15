from __future__ import annotations

import os
from pathlib import Path

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.scanner import scan_directory


def test_hidden_include_and_pattern_exclusions(tmp_path: Path, config: Config) -> None:
    root = tmp_path / "root"
    (root / "cache").mkdir(parents=True)
    (root / "visible.txt").write_text("visible", encoding="utf-8")
    (root / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (root / "cache" / "ignored.log").write_text("ignored", encoding="utf-8")
    result = scan_directory(
        root,
        config=config,
        include_hidden=True,
        excluded_patterns=("cache/*",),
    )
    assert {item.relative_path for item in result.files} == {"visible.txt", ".hidden.txt"}
    assert result.hidden_skipped == 0
    assert result.excluded_skipped == 1


def test_managed_paths_are_excluded_and_progress_is_reported(
    tmp_path: Path, config: Config
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    managed = root / "Organized"
    managed.mkdir()
    (managed / "old.txt").write_text("old", encoding="utf-8")
    (root / "new.txt").write_text("new", encoding="utf-8")
    seen = []
    result = scan_directory(
        root,
        config=config,
        excluded_paths=(managed, config.config_file),
        progress=seen.append,
    )
    assert [item.relative_path for item in result.files] == ["new.txt"]
    assert len(seen) == 1
    assert seen[0].files_seen == 1
    assert result.excluded_skipped == 1


def test_signature_read_failure_keeps_file_record_and_adds_warning(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.scanner as scanner_module

    root = tmp_path / "root"
    root.mkdir()
    file_path = root / "unreadable.pdf"
    file_path.write_bytes(b"%PDF-1.4")

    def fail_header(_path: Path, **_kwargs: object) -> bytes:
        raise PermissionError("simulated signature read failure")

    monkeypatch.setattr(scanner_module, "read_file_header", fail_header)
    result = scan_directory(root, config=config)
    assert len(result.files) == 1
    assert result.files[0].category == "Documents"
    assert "simulated signature read failure" in (result.files[0].warning or "")
    assert result.issues[0].operation == "read_signature"


def test_special_files_are_skipped_when_fifo_is_available(tmp_path: Path, config: Config) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is not available on this platform")
    root = tmp_path / "root"
    root.mkdir()
    os.mkfifo(root / "pipe")
    result = scan_directory(root, config=config)
    assert result.files == []
    assert result.special_files_skipped == 1


def test_scan_reports_directory_permission_failure_without_aborting(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.scanner as scanner_module

    root = tmp_path / "root"
    denied = root / "denied"
    denied.mkdir(parents=True)
    original = scanner_module.os.scandir

    def fail(path: os.PathLike[str] | str):
        if Path(path) == denied:
            raise PermissionError("permission denied")
        return original(path)

    monkeypatch.setattr(scanner_module.os, "scandir", fail)
    result = scan_directory(root, config=config)
    assert any(issue.operation == "scan_directory" for issue in result.issues)
    assert result.directories_scanned == 1
