from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import make_png, try_symlink
from smart_file_organizer.classifier import classify_file, identify_signature
from smart_file_organizer.config import Config
from smart_file_organizer.scanner import scan_directory
from smart_file_organizer.utils import format_bytes


def test_scan_recursively_classifies_and_filters(sample_tree: Path, config: Config) -> None:
    result = scan_directory(sample_tree, config=config, recursive=True)
    paths = {record.relative_path: record for record in result.files}
    assert "report.pdf" in paths
    assert "nested/report.pdf" in paths
    assert paths["report.pdf"].category == "Documents"
    assert paths["budget.XLSX"].category == "Spreadsheets"
    assert paths["bad.jpg"].category == "Other"
    assert "signature" in (paths["bad.jpg"].warning or "").lower()
    assert "no-extension" in paths
    assert ".hidden.txt" not in paths
    assert ".git/ignored.txt" not in paths
    assert result.hidden_skipped == 1
    assert result.excluded_skipped == 1
    assert result.directories_scanned == 2


def test_scan_non_recursive_only_reads_top_level(sample_tree: Path, config: Config) -> None:
    result = scan_directory(sample_tree, config=config, recursive=False)
    assert "nested/report.pdf" not in {record.relative_path for record in result.files}
    assert result.directories_scanned == 1


def test_scan_detects_signature_for_extensionless_file(tmp_path: Path, config: Config) -> None:
    path = tmp_path / "signatureless"
    path.write_bytes(make_png())
    result = scan_directory(tmp_path, config=config)
    record = next(file_record for file_record in result.files if file_record.path == path)
    assert record.category == "Images"
    assert record.signature == "PNG"
    assert "unrecognized extension" in (record.warning or "")


def test_classifier_handles_uppercase_missing_and_misleading_extensions(tmp_path: Path) -> None:
    assert classify_file(Path("FILE.PDF"), header=b"%PDF-1.4").category == "Documents"
    assert classify_file(Path("no-extension"), header=b"\x89PNG\r\n\x1a\n").category == "Images"
    wrong = classify_file(Path("picture.png"), header=b"plain text")
    assert wrong.category == "Other"
    assert wrong.warning
    assert classify_file(Path("unknown.bin"), header=b"arbitrary").category == "Other"


def test_classifier_accepts_container_formats_and_respects_validation_setting() -> None:
    docx = classify_file(Path("book.xlsx"), header=b"PK\x03\x04workbook")
    assert docx.category == "Spreadsheets"
    ole_doc = classify_file(Path("legacy.doc"), header=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1office")
    assert ole_doc.category == "Documents"
    mismatched = classify_file(Path("notes.txt"), header=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1office")
    assert mismatched.category == "Other"
    m4a = classify_file(Path("song.m4a"), header=b"\x00\x00\x00\x18ftypM4A ")
    assert m4a.category == "Audio"
    config = Config(signature_validation=False)
    assert (
        classify_file(Path("bad.pdf"), config=config, header=b"plain text").category == "Documents"
    )


def test_signature_identification_uses_only_small_headers() -> None:
    assert identify_signature(b"%PDF-1.4") == "PDF"
    assert identify_signature(b"PK\x03\x04abc") == "ZIP"
    assert identify_signature(b"RIFFxxxxWAVEfmt ") == "WAVE"
    assert identify_signature(b"RIFFxxxxAVI ") == "AVI"
    assert identify_signature(b"RIFFxxxxWEBP") == "WEBP"
    assert identify_signature(b"\xff\xfb\x90\x64") == "MPEG_AUDIO"
    tar_header = bytearray(262)
    tar_header[257:262] = b"ustar"
    assert identify_signature(bytes(tar_header)) == "TAR"
    assert identify_signature(b"nothing known") is None


def test_signature_category_mismatch_is_classified_as_other() -> None:
    mismatch = classify_file(Path("notes.txt"), header=b"\x89PNG\r\n\x1a\n")
    assert mismatch.category == "Other"
    assert "suggests Images" in (mismatch.warning or "")


def test_scan_skips_symlinks_and_does_not_escape_root(tmp_path: Path, config: Config) -> None:
    root = tmp_path / "selected"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_text("outside", encoding="utf-8")
    (root / "inside.txt").write_text("inside", encoding="utf-8")
    created = try_symlink(outside, root / "linked", is_directory=True)
    if not created:
        pytest.skip("Symlinks are not permitted in this environment")
    result = scan_directory(root, config=config)
    assert {record.relative_path for record in result.files} == {"inside.txt"}
    assert result.symlinks_skipped == 1


def test_scan_handles_unreadable_subdirectory_as_issue(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.scanner as scanner_module

    root = tmp_path / "root"
    blocked = root / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "visible.txt").write_text("visible", encoding="utf-8")
    original_scandir = scanner_module.os.scandir

    def denied(path: os.PathLike[str] | str):
        if Path(path) == blocked:
            raise PermissionError("simulated permission denial")
        return original_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", denied)
    result = scan_directory(root, config=config)
    assert "visible.txt" in {record.relative_path for record in result.files}
    assert any("simulated permission denial" in issue.message for issue in result.issues)


def test_scan_rejects_missing_and_non_directory_paths(tmp_path: Path, config: Config) -> None:
    with pytest.raises(FileNotFoundError):
        scan_directory(tmp_path / "missing", config=config)
    file_path = tmp_path / "a-file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        scan_directory(file_path, config=config)


def test_scan_does_not_modify_files(sample_tree: Path, config: Config) -> None:
    before = {
        path.relative_to(sample_tree): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    scan_directory(sample_tree, config=config)
    after = {
        path.relative_to(sample_tree): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_format_bytes_is_human_readable() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(-1) == "0 B"
