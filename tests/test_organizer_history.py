from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.history import ManifestStore, recover_operation, undo_operation
from smart_file_organizer.organizer import apply_plan, build_organization_plan
from smart_file_organizer.scanner import scan_directory
from smart_file_organizer.utils import collision_key, safe_component, safe_filename


def _plan(root: Path, config: Config, destination: Path | None = None):
    scan = scan_directory(root, config=config, excluded_paths=(destination,) if destination else ())
    return build_organization_plan(scan, destination or root / "Organized", config=config)


def test_preview_is_deterministic_and_never_moves_files(sample_tree: Path, config: Config) -> None:
    before = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    plan = _plan(sample_tree, config)
    after = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert len(plan.items) > 0
    conflict = [item for item in plan.items if "report (" in item.destination.name]
    assert conflict
    first = [(item.source, item.destination) for item in plan.items]
    second = [(item.source, item.destination) for item in _plan(sample_tree, config).items]
    assert first == second


def test_apply_manifest_persists_and_undo_restores_files(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    original = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    plan = _plan(sample_tree, config)
    store = ManifestStore(tmp_path / "state")
    manifest = apply_plan(plan, store, chunk_size=4096)
    assert manifest.status == "completed"
    assert all(item.status == "moved" for item in manifest.items)
    loaded = ManifestStore(tmp_path / "state").load(manifest.run_id)
    assert loaded.run_id == manifest.run_id
    assert not any(Path(item.source).exists() for item in manifest.items)
    assert all(Path(item.destination).is_file() for item in manifest.items)
    restored = undo_operation(ManifestStore(tmp_path / "state"), manifest.run_id, chunk_size=4096)
    assert restored.status == "undone"
    current = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    assert current == original


def test_organize_is_idempotent_after_managed_destination_is_excluded(
    sample_tree: Path, config: Config
) -> None:
    destination = sample_tree / "Organized"
    first = _plan(sample_tree, config, destination)
    result = apply_plan(first, ManifestStore(Path(config.storage_dir)), chunk_size=4096)
    assert result.status == "completed"
    rescan = scan_directory(sample_tree, config=config, excluded_paths=(destination,))
    second = build_organization_plan(rescan, destination, config=config)
    assert second.items == []
    assert second.skipped_count == 0


def test_changed_source_after_preview_is_skipped_without_moving(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    plan = _plan(sample_tree, config)
    item = plan.items[0]
    Path(item.source).write_bytes(b"changed after preview")
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    matching = next(op for op in manifest.items if op.source == str(item.source))
    assert matching.status == "failed"
    assert Path(item.source).exists()
    assert not Path(item.destination).exists()


def test_destination_created_after_preview_is_never_overwritten(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    plan = _plan(sample_tree, config)
    item = plan.items[0]
    destination = Path(item.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("user's existing content", encoding="utf-8")
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    matching = next(op for op in manifest.items if op.source == str(item.source))
    assert matching.status == "failed"
    assert destination.read_text(encoding="utf-8") == "user's existing content"
    assert Path(item.source).exists()


def test_undo_skips_modified_destination_and_occupied_original(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    plan = _plan(sample_tree, config)
    store = ManifestStore(tmp_path / "state")
    manifest = apply_plan(plan, store, chunk_size=4096)
    first, second = manifest.items[:2]
    Path(first.destination).write_text("modified after organize", encoding="utf-8")
    Path(second.source).write_text("new file appeared at original path", encoding="utf-8")
    undone = undo_operation(store, manifest.run_id, chunk_size=4096)
    item_first = next(item for item in undone.items if item.source == first.source)
    item_second = next(item for item in undone.items if item.source == second.source)
    assert item_first.undo_status == "skipped"
    assert item_second.undo_status == "skipped"
    assert undone.status == "partially_undone"
    assert Path(first.destination).read_text(encoding="utf-8") == "modified after organize"
    assert Path(second.source).read_text(encoding="utf-8") == "new file appeared at original path"


def test_destination_symlink_escape_is_rejected_without_touching_outside(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    destination = sample_tree / "Organized"
    plan = _plan(sample_tree, config, destination)
    item = plan.items[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    category_dir = destination / item.category
    category_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        category_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not permitted in this environment")
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    matching = next(op for op in manifest.items if op.source == str(item.source))
    assert matching.status == "failed"
    assert Path(item.source).exists()
    assert list(outside.iterdir()) == []


def test_partial_move_keeps_source_and_destination_when_source_unlink_fails(
    sample_tree: Path, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.file_ops as file_ops

    plan = _plan(sample_tree, config)
    item = plan.items[0]
    source = Path(item.source)
    original_unlink = Path.unlink

    def failing_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == source:
            raise PermissionError("simulated locked source")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(file_ops.Path, "unlink", failing_unlink)
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"), chunk_size=4096)
    matching = next(op for op in manifest.items if op.source == str(source))
    assert matching.status == "partial"
    assert source.exists()
    assert Path(matching.destination).exists()
    assert "source" in (matching.error or "").lower()


def test_recover_reconciles_verified_destination_after_interrupted_manifest(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    store = ManifestStore(tmp_path / "state")
    manifest = apply_plan(_plan(sample_tree, config), store, chunk_size=4096)
    manifest.items[0].status = "moving"
    manifest.status = "running"
    store.save(manifest)
    recovered = recover_operation(store, manifest.run_id)
    assert recovered.items[0].status == "moved"
    assert "Verified destination" in (recovered.items[0].recovery_note or "")


def test_filename_sanitization_handles_reserved_names_and_long_unicode() -> None:
    assert safe_filename("CON.txt").startswith("_CON")
    assert "/" not in safe_filename("../bad:name.txt")
    long_name = safe_filename("é" * 200 + ".txt")
    assert len(long_name.encode("utf-8")) <= 240
    assert safe_component("../outside") not in {"..", ".", ""}
    assert collision_key(Path("A.txt"), case_sensitive=False) == collision_key(
        Path("a.txt"), case_sensitive=False
    )
    assert collision_key(Path("A.txt"), case_sensitive=True) != collision_key(
        Path("a.txt"), case_sensitive=True
    )


def test_destination_equal_to_root_or_ancestor_is_rejected(
    sample_tree: Path, config: Config
) -> None:
    scan = scan_directory(sample_tree, config=config)
    with pytest.raises(Exception, match="itself"):
        build_organization_plan(scan, sample_tree, config=config)
    with pytest.raises(Exception, match="ancestor"):
        build_organization_plan(scan, sample_tree.parent, config=config)


def test_apply_empty_plan_writes_a_valid_completed_manifest(tmp_path: Path, config: Config) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    scan = scan_directory(root, config=config)
    plan = build_organization_plan(scan, root / "Organized", config=config)
    manifest = apply_plan(plan, ManifestStore(tmp_path / "state"))
    assert manifest.status == "completed"
    assert manifest.items == []


def test_grouping_and_filename_templates_are_applied_safely(
    sample_tree: Path, config: Config
) -> None:
    config.filename_template = "{stem} - Sorted{suffix}"
    scan = scan_directory(sample_tree, config=config)
    plan = build_organization_plan(
        scan,
        sample_tree / "Organized",
        config=config,
        by_month=True,
        by_extension=True,
    )
    for item in plan.items:
        record = next(record for record in scan.files if record.path == item.source)
        modified = datetime.fromtimestamp(record.mtime_ns / 1_000_000_000)
        expected_name = safe_filename(item.source.stem + " - Sorted" + item.source.suffix)
        assert item.destination.name.startswith(expected_name.removesuffix(item.source.suffix))
        assert item.destination.suffix == item.source.suffix
        assert modified.strftime("%Y") in item.destination.parts
        assert modified.strftime("%m") in item.destination.parts
        assert (record.extension.lstrip(".") or "no-extension") in item.destination.parts
        assert item.destination.resolve(strict=False).is_relative_to(plan.destination_root)


def test_existing_destination_and_casefold_collisions_get_deterministic_suffixes(
    tmp_path: Path, config: Config
) -> None:
    root = tmp_path / "collisions"
    (root / "one").mkdir(parents=True)
    (root / "two").mkdir()
    (root / "one" / "Alpha.txt").write_text("A", encoding="utf-8")
    (root / "two" / "alpha.txt").write_text("a", encoding="utf-8")
    destination = root / "Organized"
    desired = destination / "Documents" / "Alpha.txt"
    desired.parent.mkdir(parents=True)
    desired.write_text("preexisting", encoding="utf-8")
    scan = scan_directory(root, config=config, excluded_paths=(destination,))
    plan = build_organization_plan(scan, destination, config=config, case_sensitive=False)
    names = {item.destination.name for item in plan.items}
    assert "Alpha (1).txt" in names or "alpha (1).txt" in names
    assert len({item.destination.name.casefold() for item in plan.items}) == len(plan.items)
    assert desired.read_text(encoding="utf-8") == "preexisting"
