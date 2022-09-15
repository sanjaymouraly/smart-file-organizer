from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.history import (
    ManifestError,
    ManifestStore,
    recover_operation,
    undo_operation,
)
from smart_file_organizer.models import ManifestItem, OperationManifest
from smart_file_organizer.organizer import apply_plan, build_organization_plan
from smart_file_organizer.scanner import scan_directory
from smart_file_organizer.utils import iso_timestamp


def _applied(root: Path, config: Config, state: Path):
    plan = build_organization_plan(
        scan_directory(root, config=config), root / "Organized", config=config
    )
    store = ManifestStore(state)
    manifest = apply_plan(plan, store, chunk_size=4096)
    return store, manifest


def test_manifest_listing_reports_directory_errors_and_digest_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.history as history_module

    store = ManifestStore(tmp_path / "state")
    store.manifest_dir.mkdir(parents=True)
    original_glob = Path.glob
    monkeypatch.setattr(
        Path,
        "glob",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("list denied")),
    )
    manifests, errors = store.list_manifests()
    assert manifests == []
    assert errors == ["Cannot list manifest directory: list denied"]
    monkeypatch.setattr(Path, "glob", original_glob)

    target = tmp_path / "target.txt"
    target.write_text("data", encoding="utf-8")
    monkeypatch.setattr(
        history_module,
        "hash_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("read denied")),
    )
    assert history_module._file_digest(target) is None
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not permitted in this environment")
    assert history_module._file_digest(link) is None


def test_manifest_store_rejects_invalid_ids_and_reports_corrupt_manifests(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "state")
    with pytest.raises(ManifestError, match="32-character"):
        store.load("../escape")
    store.manifest_dir.mkdir(parents=True)
    (store.manifest_dir / f"{'a' * 32}.json").write_text("{bad", encoding="utf-8")
    manifests, errors = store.list_manifests()
    assert manifests == []
    assert errors and "Cannot load" in errors[0]


def test_manifest_store_rejects_unsupported_schema_and_id_mismatch(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "state")
    store.manifest_dir.mkdir(parents=True)
    run_id = uuid.uuid4().hex
    path = store.manifest_dir / f"{run_id}.json"
    path.write_text('{"schema_version": 999, "run_id": "' + run_id + '"}', encoding="utf-8")
    with pytest.raises(ManifestError, match="Unsupported manifest schema"):
        store.load(run_id)
    mismatched = OperationManifest(
        run_id="b" * 32,
        kind="organize",
        created_at=iso_timestamp(),
        updated_at=iso_timestamp(),
        status="completed",
        root=str(tmp_path),
        destination_root=str(tmp_path / "dest"),
        items=[],
    )
    path.write_text(json.dumps(mismatched.to_dict()), encoding="utf-8")
    with pytest.raises(ManifestError, match="does not match"):
        store.load(run_id)


def test_recover_leaves_two_verified_copies_untouched(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    item = manifest.items[0]
    shutil.copyfile(item.destination, item.source)
    item.status = "moving"
    manifest.status = "running"
    store.save(manifest)
    before_source = Path(item.source).read_bytes()
    before_destination = Path(item.destination).read_bytes()
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].status == "partial"
    assert "Both verified copies" in (recovered.items[0].recovery_note or "")
    assert Path(item.source).read_bytes() == before_source
    assert Path(item.destination).read_bytes() == before_destination


def test_recover_marks_interrupted_when_source_remains_without_destination(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    item = manifest.items[0]
    shutil.copyfile(item.destination, item.source)
    Path(item.destination).unlink()
    item.status = "moving"
    manifest.status = "running"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].status == "interrupted"
    assert Path(item.source).exists()
    assert not Path(item.destination).exists()


def test_recover_preserves_two_paths_with_unverified_content(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    item = manifest.items[0]
    shutil.copyfile(item.destination, item.source)
    Path(item.destination).write_text("changed destination", encoding="utf-8")
    item.status = "moving"
    manifest.status = "running"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].status == "interrupted"
    assert "does not match" in (recovered.items[0].recovery_note or "")
    assert Path(item.source).exists()
    assert Path(item.destination).read_text(encoding="utf-8") == "changed destination"


def test_recover_marks_pending_items_as_interrupted_without_touching_source(
    tmp_path: Path,
) -> None:
    store = ManifestStore(tmp_path / "state")
    root = tmp_path / "root"
    root.mkdir()
    source = root / "pending.txt"
    source.write_text("still here", encoding="utf-8")
    now = iso_timestamp()
    run_id = uuid.uuid4().hex
    item = ManifestItem(
        source=str(source),
        destination=str(root / "Organized" / "pending.txt"),
        category="Documents",
        size=10,
        original_snapshot={},
        expected_sha256="0" * 64,
        status="pending",
    )
    manifest = OperationManifest(
        run_id=run_id,
        kind="organize",
        created_at=now,
        updated_at=now,
        status="running",
        root=str(root),
        destination_root=str(root / "Organized"),
        items=[item],
    )
    store.save(manifest)
    recovered = recover_operation(store, run_id)
    assert recovered.items[0].status == "interrupted"
    assert source.read_text(encoding="utf-8") == "still here"
    assert not Path(item.destination).exists()


def test_recover_does_not_trust_manifest_paths_outside_operation_root(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    manifest.items[0].source = str(outside)
    manifest.items[0].status = "moving"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].status == "interrupted"
    assert "escapes operation root" in (recovered.items[0].recovery_note or "")
    assert outside.read_text(encoding="utf-8") == "keep"


def test_recover_handles_interrupted_undo_states(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    first = manifest.items[0]
    first.undo_status = "moving"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].undo_status == "pending"
    assert "safe to retry" in (recovered.items[0].recovery_note or "")

    # Ambiguous state with both paths is left untouched and requires attention.
    shutil.copyfile(first.destination, first.source)
    recovered.items[0].undo_status = "moving"
    store.save(recovered)
    recovered_again = recover_operation(store, manifest.run_id)
    assert recovered_again.items[0].undo_status == "needs_attention"
    assert Path(first.source).exists() and Path(first.destination).exists()


def test_undo_retries_after_recovery_marks_move_pending(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    item = manifest.items[0]
    item.undo_status = "moving"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].undo_status == "pending"
    undone = undo_operation(store, manifest.run_id, chunk_size=4096)
    assert undone.items[0].undo_status == "undone"
    assert Path(item.source).exists()
    assert not Path(item.destination).exists()
    assert undone.metadata["undo_duration_seconds"] >= 0


def test_undo_recovers_already_restored_original_after_interruption(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    item = manifest.items[0]
    Path(item.destination).replace(item.source)
    item.undo_status = "moving"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].undo_status == "undone"
    assert recovered.items[0].recovery_note


def test_undo_does_not_move_a_file_when_manifest_paths_are_tampered(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    manifest.items[0].source = str(outside)
    store.save(manifest)
    undone = undo_operation(store, manifest.run_id, chunk_size=4096)
    assert outside.read_text(encoding="utf-8") == "private"
    assert undone.items[0].status == "interrupted"
    assert undone.items[0].undo_status == "not_requested"


def test_undo_skips_missing_or_symlink_destination(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    missing_item = manifest.items[0]
    Path(missing_item.destination).unlink()
    missing_result = undo_operation(store, manifest.run_id, chunk_size=4096)
    assert missing_result.items[0].undo_status == "skipped"
    assert "missing or unsafe" in (missing_result.items[0].undo_error or "")

    second_store, second_manifest = _applied(sample_tree, config, tmp_path / "state-second")
    symlink_item = second_manifest.items[0]
    destination = Path(symlink_item.destination)
    target = destination.parent / "safe-target.txt"
    target.write_text("preserve", encoding="utf-8")
    destination.unlink()
    try:
        destination.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not permitted in this environment")
    result = undo_operation(second_store, second_manifest.run_id, chunk_size=4096)
    assert result.items[0].undo_status == "skipped"
    assert target.read_text(encoding="utf-8") == "preserve"
    assert destination.is_symlink()


def test_undo_fails_safely_if_mover_reports_an_error(
    sample_tree: Path,
    config: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smart_file_organizer.history as history_module

    store, manifest = _applied(sample_tree, config, tmp_path / "state")
    destination = Path(manifest.items[0].destination)

    def fail_move(*_args: object, **_kwargs: object):
        raise PermissionError("simulated locked destination")

    monkeypatch.setattr(history_module, "move_file_safely", fail_move)
    undone = undo_operation(store, manifest.run_id, chunk_size=4096)
    assert undone.items[0].undo_status == "failed"
    assert destination.exists()


def test_undo_with_no_completed_moves_returns_or_raises_safely(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "state")
    now = iso_timestamp()
    empty = OperationManifest(
        run_id=uuid.uuid4().hex,
        kind="organize",
        created_at=now,
        updated_at=now,
        status="completed",
        root=str(tmp_path),
        destination_root=str(tmp_path / "dest"),
        items=[],
    )
    store.save(empty)
    with pytest.raises(ManifestError, match="no files"):
        undo_operation(store, empty.run_id)

    failed = OperationManifest(
        run_id=uuid.uuid4().hex,
        kind="organize",
        created_at=now,
        updated_at=now,
        status="failed",
        root=str(tmp_path),
        destination_root=str(tmp_path / "dest"),
        items=[
            ManifestItem(
                source=str(tmp_path / "missing"),
                destination=str(tmp_path / "dest" / "missing"),
                category="Other",
                size=0,
                original_snapshot={},
                expected_sha256="0" * 64,
                status="failed",
            )
        ],
    )
    store.save(failed)
    assert undo_operation(store, failed.run_id).status == "failed"
