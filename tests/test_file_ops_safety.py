from __future__ import annotations

import errno
from pathlib import Path

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.file_ops import (
    _publish_temp,
    _same_snapshot,
    _unlink_if_same_file,
    move_file_safely,
)
from smart_file_organizer.history import ManifestStore
from smart_file_organizer.models import FileSnapshot
from smart_file_organizer.organizer import apply_plan, build_organization_plan
from smart_file_organizer.scanner import scan_directory
from smart_file_organizer.utils import FileChangedError, FileSafetyError, hash_file, snapshot_path


def test_filesystem_hardlink_failure_uses_exclusive_copy_fallback(
    sample_tree: Path, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.file_ops as file_ops

    plan = build_organization_plan(
        scan_directory(sample_tree, config=config), sample_tree / "Organized", config=config
    )
    monkeypatch.setattr(
        file_ops.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EPERM, "no hard links")),
    )
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    assert manifest.status == "completed"
    assert all(Path(item.destination).is_file() for item in manifest.items)
    assert all(not Path(item.source).exists() for item in manifest.items)


def test_fallback_copy_failure_leaves_source_and_records_partial_state(
    sample_tree: Path, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.file_ops as file_ops

    plan = build_organization_plan(
        scan_directory(sample_tree, config=config), sample_tree / "Organized", config=config
    )
    selected = plan.items[0]
    monkeypatch.setattr(
        file_ops.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EPERM, "no hard links")),
    )
    monkeypatch.setattr(
        file_ops.shutil,
        "copyfileobj",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated disk full")),
    )
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    item = next(record for record in manifest.items if record.source == str(selected.source))
    assert item.status == "partial"
    assert Path(selected.source).exists()
    assert Path(selected.destination).exists()
    assert "interrupted" in (item.error or "")


def test_destination_verification_failure_never_removes_source(
    sample_tree: Path,
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smart_file_organizer.file_ops as file_ops

    plan = build_organization_plan(
        scan_directory(sample_tree, config=config), sample_tree / "Organized", config=config
    )
    selected = plan.items[0]
    original_hash = file_ops.hash_file

    def bad_destination_hash(path: Path, **kwargs: object) -> str:
        if path == selected.destination:
            return "0" * 64
        return original_hash(path, **kwargs)

    monkeypatch.setattr(file_ops, "hash_file", bad_destination_hash)
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    item = next(record for record in manifest.items if record.source == str(selected.source))
    assert item.status == "partial"
    assert Path(selected.source).exists()
    assert Path(selected.destination).exists()
    assert "verification failed" in (item.error or "")


def test_stale_temporary_name_does_not_get_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "second-source.txt"
    source.write_text("stable", encoding="utf-8")
    source_snapshot = snapshot_path(source)
    destination = tmp_path / "second-destination.txt"
    temporary = tmp_path / ".operation-temp"
    temporary.write_text("user-owned temp", encoding="utf-8")
    with pytest.raises(FileExistsError):
        move_file_safely(
            source,
            destination,
            source_root=tmp_path,
            destination_root=tmp_path,
            expected_snapshot=source_snapshot,
            expected_sha256=hash_file(source),
            temporary_path=temporary,
            chunk_size=4096,
        )
    assert source.read_text(encoding="utf-8") == "stable"
    assert temporary.read_text(encoding="utf-8") == "user-owned temp"


def test_publish_refuses_existing_destination(tmp_path: Path) -> None:
    temporary = tmp_path / "temp"
    destination = tmp_path / "destination"
    temporary.write_text("temp", encoding="utf-8")
    destination.write_text("owned", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _publish_temp(temporary, destination)
    assert destination.read_text(encoding="utf-8") == "owned"
    assert temporary.read_text(encoding="utf-8") == "temp"


def test_unlink_helper_requires_matching_regular_file(tmp_path: Path) -> None:
    file_path = tmp_path / "file"
    file_path.write_text("owned", encoding="utf-8")
    info = file_path.stat()
    identity = (info.st_dev, info.st_ino)
    if all(identity):
        assert not _unlink_if_same_file(file_path, (identity[0], identity[1] + 1))
        assert file_path.exists()
    else:
        pytest.skip("This filesystem does not expose stable device/inode identity values")
    assert not _unlink_if_same_file(tmp_path, None)
    assert _unlink_if_same_file(file_path, identity)
    assert not file_path.exists()
    assert not _unlink_if_same_file(file_path, identity)
    snapshot = FileSnapshot.from_stat(info)
    assert not _same_snapshot(snapshot, file_path)


def test_move_rejects_symlink_source_and_outside_destination(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "target.txt"
    target.write_text("outside", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not permitted in this environment")
    dummy_snapshot = FileSnapshot(
        size=7, mtime_ns=0, ctime_ns=0, atime_ns=0, device=0, inode=0, links=1
    )
    internal_target = root / "internal-target.txt"
    internal_target.write_text("inside", encoding="utf-8")
    internal_link = root / "internal-link.txt"
    internal_link.symlink_to(internal_target)
    with pytest.raises(FileSafetyError, match="Symbolic-link sources"):
        move_file_safely(
            internal_link,
            root / "dest-internal.txt",
            source_root=root,
            destination_root=root,
            expected_snapshot=dummy_snapshot,
            expected_sha256="0" * 64,
            temporary_path=root / ".tmp-internal",
        )
    with pytest.raises(FileSafetyError):
        move_file_safely(
            link,
            root / "dest.txt",
            source_root=root,
            destination_root=root,
            expected_snapshot=dummy_snapshot,
            expected_sha256="0" * 64,
            temporary_path=root / ".tmp",
        )
    regular = root / "regular.txt"
    regular.write_text("inside", encoding="utf-8")
    snapshot = snapshot_path(regular)
    with pytest.raises(FileSafetyError):
        move_file_safely(
            regular,
            outside / "escaped.txt",
            source_root=root,
            destination_root=root,
            expected_snapshot=snapshot,
            expected_sha256=hash_file(regular),
            temporary_path=outside / ".temp",
        )
    assert regular.exists()
    assert not (outside / "escaped.txt").exists()


def test_temp_path_must_be_beside_destination(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source"
    source.write_bytes(b"data")
    snapshot = snapshot_path(source)
    with pytest.raises(FileSafetyError, match="beside"):
        move_file_safely(
            source,
            root / "dest",
            source_root=root,
            destination_root=root,
            expected_snapshot=snapshot,
            expected_sha256=hash_file(source),
            temporary_path=root / "elsewhere" / ".temp",
        )
    assert source.exists()


def test_source_replaced_between_preflight_and_copy_is_left_at_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.file_ops as file_ops

    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.bin"
    source.write_bytes(b"original")
    snapshot = snapshot_path(source)
    digest = hash_file(source)
    original_open = file_ops.open_regular_readonly
    calls = 0

    def replace_before_open(path: Path):
        nonlocal calls
        calls += 1
        if path == source:
            source.write_bytes(b"changed externally")
        return original_open(path)

    monkeypatch.setattr(file_ops, "open_regular_readonly", replace_before_open)
    with pytest.raises(FileChangedError, match="Source changed since preview"):
        move_file_safely(
            source,
            root / "destination.bin",
            source_root=root,
            destination_root=root,
            expected_snapshot=snapshot,
            expected_sha256=digest,
            temporary_path=root / ".stage.tmp",
            chunk_size=4096,
        )
    assert calls == 1
    assert source.read_bytes() == b"changed externally"
    assert not (root / "destination.bin").exists()
    assert not (root / ".stage.tmp").exists()


def test_move_rejects_same_path_and_tampered_preview_digest(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source"
    source.write_bytes(b"data")
    snapshot = snapshot_path(source)
    digest = hash_file(source)
    with pytest.raises(FileSafetyError, match="same path"):
        move_file_safely(
            source,
            source,
            source_root=root,
            destination_root=root,
            expected_snapshot=snapshot,
            expected_sha256=digest,
            temporary_path=root / ".temp",
        )
    with pytest.raises(FileChangedError, match="contents changed since preview"):
        move_file_safely(
            source,
            root / "destination",
            source_root=root,
            destination_root=root,
            expected_snapshot=snapshot,
            expected_sha256="0" * 64,
            temporary_path=root / ".stage.tmp",
            chunk_size=4096,
        )
    assert source.exists()
    assert not (root / "destination").exists()
