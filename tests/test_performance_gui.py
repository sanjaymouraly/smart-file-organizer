from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.gui import launch_gui
from smart_file_organizer.scanner import scan_directory
from smart_file_organizer.utils import hash_file


def test_scanner_handles_thousands_of_generated_files(tmp_path: Path, config: Config) -> None:
    root = tmp_path / "many"
    root.mkdir()
    for index in range(1200):
        (root / f"file {index:04d}.txt").write_text(f"sample {index}\n", encoding="utf-8")
    result = scan_directory(root, config=config, recursive=False)
    assert len(result.files) == 1200
    assert result.directories_scanned == 1
    assert result.total_bytes > 0


def test_sha256_streaming_memory_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "large.bin"
    chunk = b"0123456789abcdef" * 65536
    with path.open("wb") as stream:
        for _ in range(16):
            stream.write(chunk)
    tracemalloc.start()
    try:
        digest = hash_file(path, chunk_size=64 * 1024)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(digest) == 64
    assert peak < 4 * 1024 * 1024


def test_gui_reports_headless_display_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkinter

    class HeadlessError(tkinter.TclError):
        pass

    def no_display() -> None:
        raise HeadlessError("no display")

    monkeypatch.setattr(tkinter, "Tk", no_display)
    with pytest.raises(RuntimeError, match="no graphical display"):
        launch_gui(Config())


def test_gui_command_keeps_cli_available_without_display(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import tkinter

    from smart_file_organizer.cli import main

    monkeypatch.setattr(
        tkinter, "Tk", lambda: (_ for _ in ()).throw(tkinter.TclError("no display"))
    )
    result = main(["gui"])
    assert result == 1
    assert "no graphical display" in capsys.readouterr().err
