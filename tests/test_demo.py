from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from smart_file_organizer.demo import create_demo_data


def test_demo_generator_creates_valid_synthetic_mixed_dataset(tmp_path: Path) -> None:
    root = create_demo_data(tmp_path / "generated")
    assert (root / "report.pdf").read_bytes().startswith(b"%PDF-1.4")
    assert (root / "holiday.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (root / "budget.csv").read_text(encoding="utf-8").startswith("Item,Amount")
    assert (root / "notes.txt").read_bytes() == (root / "notes-copy.txt").read_bytes()
    assert (root / "report.pdf").read_bytes() == (root / "nested" / "report.pdf").read_bytes()
    with ZipFile(root / "archive.zip") as archive:
        assert "README.txt" in archive.namelist()
    with ZipFile(root / "budget.xlsx") as workbook:
        assert "xl/workbook.xml" in workbook.namelist()
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
    assert any("résumé" in path.name for path in root.iterdir())
    assert (root / ".hidden-demo.txt").exists()


def test_source_checkout_demo_script_creates_only_synthetic_files(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_demo.py"
    target = tmp_path / "script-demo"
    result = subprocess.run(
        [sys.executable, str(script), str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Created synthetic demo files" in result.stdout
    assert (target / "report.pdf").read_bytes().startswith(b"%PDF-1.4")
    assert (target / "notes.txt").read_bytes() == (target / "notes-copy.txt").read_bytes()


def test_demo_generator_accepts_existing_empty_directory_and_never_overwrites(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert create_demo_data(empty) == empty.resolve()
    with pytest.raises(FileExistsError, match="non-empty"):
        create_demo_data(empty)
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("keep", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        create_demo_data(file_path)
