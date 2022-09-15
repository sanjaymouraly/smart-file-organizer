from __future__ import annotations

import os
from pathlib import Path

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.duplicates import (
    estimate_reclaimable_bytes,
    find_duplicates,
    select_retained_copies,
)
from smart_file_organizer.models import DuplicateFile, FileSnapshot
from smart_file_organizer.scanner import scan_directory


def _hardlink_or_skip(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError):
        pytest.skip("Hard links are not available in this environment")


def test_finds_exact_duplicates_with_different_names(sample_tree: Path, config: Config) -> None:
    scan = scan_directory(sample_tree, config=config)
    analysis = find_duplicates(scan.files, config=config)
    assert len(analysis.groups) == 2
    paths_by_group = [{item.path.name for item in group.files} for group in analysis.groups]
    assert {"notes.txt", "notes copy.txt"} in paths_by_group
    assert {"report.pdf"} <= set.union(*paths_by_group)
    assert analysis.duplicate_file_count == 4
    assert analysis.files_hashed >= 2
    assert analysis.reclaimable_bytes > 0


def test_hashing_is_chunked_and_zero_byte_files_are_duplicates(
    tmp_path: Path, config: Config
) -> None:
    (tmp_path / "zero-a").write_bytes(b"")
    (tmp_path / "zero-b").write_bytes(b"")
    result = find_duplicates(
        scan_directory(tmp_path, config=config).files, config=config, chunk_size=4096
    )
    assert len(result.groups) == 1
    assert result.groups[0].size == 0
    assert result.groups[0].sha256


def test_hardlinks_are_not_counted_as_physically_reclaimable(
    tmp_path: Path, config: Config
) -> None:
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    independent = tmp_path / "c.bin"
    first.write_bytes(b"same bytes")
    _hardlink_or_skip(first, second)
    independent.write_bytes(b"same bytes")
    records = scan_directory(tmp_path, config=config).files
    group = find_duplicates(records, config=config).groups[0]
    # Keeping one of the hard-linked names cannot free that inode, but the independent copy can.
    assert group.reclaimable_bytes == len(b"same bytes")
    assert estimate_reclaimable_bytes(group.files, group.default_keep) == len(b"same bytes")


def test_all_duplicates_being_hardlinks_have_zero_reclaimable_bytes(
    tmp_path: Path, config: Config
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"linked")
    _hardlink_or_skip(first, second)
    group = find_duplicates(scan_directory(tmp_path, config=config).files, config=config).groups[0]
    assert group.reclaimable_bytes == 0


def test_reclaimable_estimate_falls_back_to_paths_without_inode_ids(tmp_path: Path) -> None:
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    snapshot = FileSnapshot(
        size=5,
        mtime_ns=1,
        ctime_ns=1,
        atime_ns=1,
        device=0,
        inode=0,
        links=1,
    )
    files = (
        DuplicateFile(first_path, 5, "digest", snapshot),
        DuplicateFile(second_path, 5, "digest", snapshot),
    )
    assert estimate_reclaimable_bytes(files, first_path) == 5


def test_external_hardlink_means_inode_is_not_reclaimable(tmp_path: Path) -> None:
    content = b"content"
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    outside_path = tmp_path / "outside"
    first_path.write_bytes(content)
    _hardlink_or_skip(first_path, second_path)
    _hardlink_or_skip(first_path, outside_path)
    snapshot = FileSnapshot.from_stat(first_path.stat())
    files = (
        DuplicateFile(first_path, len(content), "digest", snapshot),
        DuplicateFile(second_path, len(content), "digest", snapshot),
    )
    assert estimate_reclaimable_bytes(files, first_path) == 0


def test_unreadable_or_changed_candidates_are_reported_not_crashed(
    sample_tree: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.duplicates as duplicates_module

    scan = scan_directory(sample_tree, config=config)
    # Use one member of a real duplicate size group and simulate a read failure.
    candidate = next(record for record in scan.files if record.path.name == "notes.txt")
    original_hash = duplicates_module.hash_file

    def unreadable(path: Path, **kwargs: object) -> str:
        if path == candidate.path:
            raise PermissionError("simulated unreadable file")
        return original_hash(path, **kwargs)

    monkeypatch.setattr(duplicates_module, "hash_file", unreadable)
    analysis = find_duplicates(scan.files, config=config)
    assert any(issue.path == str(candidate.path) for issue in analysis.issues)
    assert all(
        candidate.path not in [item.path for item in group.files] for group in analysis.groups
    )


def test_cached_hardlink_digest_revalidates_every_alias_after_content_changes(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.duplicates as duplicates_module

    first = tmp_path / "hardlink-a.bin"
    second = tmp_path / "hardlink-b.bin"
    first.write_bytes(b"stable")
    _hardlink_or_skip(first, second)
    records = scan_directory(tmp_path, config=config).files
    original_hash = duplicates_module.hash_file
    calls = 0

    def hash_then_modify(path: Path, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        value = original_hash(path, **kwargs)
        if calls == 1:
            first.write_bytes(b"changed")
        return value

    monkeypatch.setattr(duplicates_module, "hash_file", hash_then_modify)
    analysis = find_duplicates(records, config=config)
    assert analysis.groups == []
    assert len(analysis.issues) == 1
    assert analysis.issues[0].path == str(second)
    assert "changed since it was scanned" in analysis.issues[0].message


def test_keep_selection_is_validated_and_defaults_deterministically(
    sample_tree: Path, config: Config
) -> None:
    analysis = find_duplicates(scan_directory(sample_tree, config=config).files, config=config)
    selections = select_retained_copies(analysis.groups)
    assert set(selections) == {group.sha256 for group in analysis.groups}
    explicit = analysis.groups[0].files[-1].path
    result = select_retained_copies(analysis.groups, [explicit])
    assert result[analysis.groups[0].sha256] == explicit.resolve()
    with pytest.raises(ValueError, match="not part of"):
        select_retained_copies(analysis.groups, [sample_tree / "unrelated.txt"])
    with pytest.raises(ValueError, match="only one"):
        select_retained_copies(
            analysis.groups,
            [analysis.groups[0].files[0].path, analysis.groups[0].files[1].path],
        )


def test_small_chunk_size_is_rejected(tmp_path: Path, config: Config) -> None:
    path = tmp_path / "a"
    path.write_bytes(b"abc")
    with pytest.raises(ValueError, match="chunk_size"):
        find_duplicates(
            scan_directory(tmp_path, config=config).files, config=config, chunk_size=100
        )
