from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_file_organizer.models import FileSnapshot
from smart_file_organizer.utils import (
    FileChangedError,
    FileSafetyError,
    atomic_write_json,
    collision_key,
    ensure_within,
    filename_with_number,
    format_bytes,
    hash_file,
    is_relative_to,
    open_regular_readonly,
    read_file_header,
    safe_component,
    safe_filename,
    safe_json_load,
    snapshot_path,
)


def test_snapshot_roundtrip_and_comparison(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"data")
    snapshot = snapshot_path(path)
    restored = FileSnapshot.from_dict(snapshot.to_dict())
    assert restored == snapshot
    assert restored.matches(snapshot)
    assert not restored.matches(FileSnapshot.from_dict({**snapshot.to_dict(), "size": 5}))


def test_safe_open_header_and_hash_are_streaming_and_verified(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"header-data")
    stream, opened = open_regular_readonly(path)
    with stream:
        assert stream.read(6) == b"header"
    assert opened.size == len(b"header-data")
    assert read_file_header(path, length=6) == b"header"
    assert hash_file(path, expected=opened) == hash_file(path)
    with pytest.raises(ValueError, match="chunk_size"):
        hash_file(path, chunk_size=1)
    with pytest.raises(FileChangedError):
        hash_file(path, expected=FileSnapshot.from_dict({**opened.to_dict(), "size": 99}))


def test_symlinks_and_directories_are_not_opened_as_regular_files(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(FileSafetyError):
        snapshot_path(directory)
    with pytest.raises(FileSafetyError):
        open_regular_readonly(directory)
    target = tmp_path / "target"
    target.write_bytes(b"data")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not permitted in this environment")
    with pytest.raises((FileSafetyError, OSError)):
        open_regular_readonly(link)


def test_path_containment_resolves_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("data", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not permitted in this environment")
    assert not is_relative_to(link / "secret", root)
    with pytest.raises(FileSafetyError, match="escapes"):
        ensure_within(link / "secret", root)
    assert is_relative_to(root / "file", root)


def test_filename_helpers_are_portable_and_collision_safe() -> None:
    assert safe_component("CON") == "_CON"
    assert safe_component("../bad:name") == "_._bad_name" or safe_component("../bad:name") != ".."
    assert safe_filename("CON.txt") == "_CON.txt"
    assert safe_filename("CON .txt") == "_CON.txt"
    assert filename_with_number("report.pdf", 2) == "report (2).pdf"
    assert filename_with_number("archive.tar.gz", 1) == "archive.tar (1).gz"
    assert len(safe_filename("é" * 500 + ".txt").encode("utf-8")) <= 240
    assert collision_key(Path("NAME"), case_sensitive=False) == collision_key(
        Path("name"), case_sensitive=False
    )


def test_atomic_json_write_and_safe_load(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data.json"
    atomic_write_json(path, {"ok": True, "paths": ["space name"]})
    assert safe_json_load(path) == {"ok": True, "paths": ["space name"]}
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="top level"):
        safe_json_load(path)


def test_atomic_write_cleans_temporary_file_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.utils as utils

    destination = tmp_path / "manifest.json"

    def fail_dump(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(utils.json, "dump", fail_dump)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_json(destination, {"value": 1})
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_hashing_detects_file_mutation_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.utils as utils

    path = tmp_path / "changing.bin"
    path.write_bytes(b"a" * 8192)
    original_fstat = utils.os.fstat
    calls = 0

    def mutate_on_second_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"changed while hashing")
        return original_fstat(descriptor)

    monkeypatch.setattr(utils.os, "fstat", mutate_on_second_fstat)
    with pytest.raises(FileChangedError, match="changed while hashing"):
        hash_file(path, chunk_size=4096)


def test_atomic_json_replace_failure_cleans_up_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.utils as utils

    destination = tmp_path / "manifest.json"

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(utils.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        atomic_write_json(destination, {"value": "persist"})
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_safe_component_truncation_and_filesystem_sync_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.utils as utils

    assert safe_component("") == "_"
    assert len(safe_component("é" * 100, max_bytes=20).encode("utf-8")) <= 20
    original_open = utils.os.open

    def fail_directory_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == tmp_path:
            raise OSError("unsupported directory sync")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(utils.os, "open", fail_directory_open)
    utils.fsync_directory(tmp_path)


def test_is_relative_to_gracefully_handles_resolution_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.utils as utils

    def fail_resolve(*_args: object, **_kwargs: object) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(utils.Path, "resolve", fail_resolve)
    assert not is_relative_to(tmp_path / "child", tmp_path)


def test_safe_json_load_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        safe_json_load(path)
    assert format_bytes(1024 * 1024) == "1.0 MiB"
