"""Persistent operation manifests, safe undo, and interrupted-run inspection."""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .file_ops import move_file_safely
from .models import FileSnapshot, OperationManifest
from .utils import (
    FileChangedError,
    FileSafetyError,
    atomic_write_json,
    hash_file,
    is_relative_to,
    iso_timestamp,
    safe_json_load,
    snapshot_path,
)

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ManifestError(ValueError):
    """Raised when operation history cannot be trusted or loaded."""


class ManifestStore:
    """JSON manifest storage with atomic writes and strict run-id validation."""

    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir).expanduser().resolve(strict=False)
        self.manifest_dir = self.storage_dir / "manifests"

    def manifest_path(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ManifestError("Operation ID must be a 32-character lowercase hexadecimal value")
        return self.manifest_dir / f"{run_id}.json"

    def save(self, manifest: OperationManifest) -> Path:
        path = self.manifest_path(manifest.run_id)
        atomic_write_json(path, manifest.to_dict())
        return path

    def load(self, run_id: str) -> OperationManifest:
        path = self.manifest_path(run_id)
        try:
            data = safe_json_load(path)
            manifest = OperationManifest.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ManifestError(f"Cannot load operation manifest {path}: {exc}") from exc
        if manifest.run_id != run_id:
            raise ManifestError(f"Manifest ID does not match its filename: {path}")
        return manifest

    def list_manifests(self) -> tuple[list[OperationManifest], list[str]]:
        """Return valid manifests newest-first and separately report corrupt files."""
        manifests: list[OperationManifest] = []
        errors: list[str] = []
        if not self.manifest_dir.exists():
            return manifests, errors
        try:
            paths = sorted(self.manifest_dir.glob("*.json"), reverse=True)
        except OSError as exc:
            return manifests, [f"Cannot list manifest directory: {exc}"]
        for path in paths:
            run_id = path.stem
            try:
                manifests.append(self.load(run_id))
            except ManifestError as exc:
                errors.append(str(exc))
        manifests.sort(key=lambda item: item.created_at, reverse=True)
        return manifests, errors


def _safe_manifest_paths(manifest: OperationManifest, item: Any) -> tuple[Path, Path, Path, Path]:
    """Validate that recorded source/destination paths still belong to their roots."""
    root = Path(manifest.root).expanduser().resolve(strict=False)
    destination_root = Path(manifest.destination_root).expanduser().resolve(strict=False)
    source = Path(item.source).expanduser()
    destination = Path(item.destination).expanduser()
    if not is_relative_to(source, root):
        raise FileSafetyError(f"Recorded source escapes operation root: {source}")
    if not is_relative_to(destination, destination_root):
        raise FileSafetyError(f"Recorded destination escapes operation destination: {destination}")
    return source, destination, root, destination_root


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _file_digest(path: Path) -> str | None:
    if not _exists(path) or path.is_symlink():
        return None
    try:
        return hash_file(path)
    except OSError:
        return None


def _mark_manifest_status(manifest: OperationManifest) -> None:
    operation_statuses = [item.status for item in manifest.items]
    failures = sum(status in {"failed", "partial", "interrupted"} for status in operation_statuses)
    successes = operation_statuses.count("moved")
    if failures and successes:
        manifest.status = "partial"
    elif failures:
        manifest.status = "failed"
    elif "pending" in operation_statuses:
        manifest.status = "interrupted"
    elif all(status == "moved" for status in operation_statuses):
        manifest.status = "completed"
    manifest.updated_at = iso_timestamp()


def recover_operation(store: ManifestStore, run_id: str) -> OperationManifest:
    """Reconcile interrupted manifest states without deleting or overwriting files.

    Recovery only advances an operation when the source is absent and the recorded,
    hash-verified destination is present. Ambiguous two-copy states are reported and
    left untouched for the user to inspect.
    """
    manifest = store.load(run_id)
    changed = False
    for item in manifest.items:
        try:
            source, destination, _, _ = _safe_manifest_paths(manifest, item)
        except FileSafetyError as exc:
            item.recovery_note = str(exc)
            item.status = "interrupted"
            changed = True
            continue
        if item.status == "pending":
            item.status = "interrupted"
            item.recovery_note = (
                "This file had not started moving when the manifest stopped updating; "
                "the source was left untouched. Create a fresh preview to retry."
            )
            changed = True
        if item.status == "moving":
            source_exists = _exists(source)
            destination_exists = _exists(destination)
            source_digest = _file_digest(source)
            destination_digest = _file_digest(destination)
            if not source_exists and destination_digest == item.expected_sha256:
                item.status = "moved"
                item.final_sha256 = item.expected_sha256
                try:
                    item.moved_snapshot = snapshot_path(destination).to_dict()
                except OSError:
                    item.moved_snapshot = None
                item.recovery_note = "Verified destination exists and the source path is absent."
                changed = True
            elif source_exists and not destination_exists:
                item.status = "interrupted"
                item.recovery_note = (
                    "Source remains and no destination was published. Any recorded temporary "
                    "file was left untouched; a new preview can be created safely."
                )
                changed = True
            elif (
                source_exists
                and destination_exists
                and source_digest == item.expected_sha256
                and (destination_digest == item.expected_sha256)
            ):
                item.status = "partial"
                item.recovery_note = (
                    "Both verified copies exist after interruption. No file was removed; "
                    "inspect both paths before deciding what to do."
                )
                changed = True
            else:
                item.status = "interrupted"
                item.recovery_note = (
                    "Filesystem state does not match the recorded preview. No files were changed."
                )
                changed = True

        if item.undo_status == "moving":
            try:
                source, destination, _, _ = _safe_manifest_paths(manifest, item)
            except FileSafetyError as exc:
                item.undo_status = "needs_attention"
                item.undo_error = str(exc)
                changed = True
                continue
            source_exists = _exists(source)
            destination_exists = _exists(destination)
            source_digest = _file_digest(source)
            destination_digest = _file_digest(destination)
            expected_digest = item.final_sha256 or item.expected_sha256
            if source_exists and not destination_exists and source_digest == expected_digest:
                item.undo_status = "undone"
                item.undo_error = None
                item.recovery_note = (
                    "Undo completed before interruption; the original path verifies."
                )
            elif not source_exists and destination_exists and destination_digest == expected_digest:
                item.undo_status = "pending"
                item.undo_error = None
                item.recovery_note = "Undo did not move the file; it is safe to retry if desired."
            elif source_exists and destination_exists:
                item.undo_status = "needs_attention"
                item.undo_error = "Both original and organized paths exist; neither was changed."
            else:
                item.undo_status = "needs_attention"
                item.undo_error = "Paths or contents do not match the undo manifest."
            changed = True

    if changed:
        _mark_manifest_status(manifest)
        store.save(manifest)
    return manifest


def undo_operation(
    store: ManifestStore, run_id: str, *, chunk_size: int = 1024 * 1024
) -> OperationManifest:
    """Safely undo moved entries without overwriting modified or occupied paths."""
    started_clock = time.monotonic()
    manifest = recover_operation(store, run_id)
    moved_items = [item for item in manifest.items if item.status == "moved"]
    if not moved_items:
        if not manifest.items:
            raise ManifestError(f"Operation {run_id} contains no files to undo")
        return manifest

    manifest.undo_updated_at = iso_timestamp()
    for index, item in enumerate(manifest.items, start=1):
        if item.status != "moved" or item.undo_status == "undone":
            continue
        try:
            source, destination, root, destination_root = _safe_manifest_paths(manifest, item)
        except FileSafetyError as exc:
            item.undo_status = "needs_attention"
            item.undo_error = str(exc)
            manifest.errors.append(f"Undo {item.source}: {exc}")
            store.save(manifest)
            continue

        if _exists(source):
            item.undo_status = "skipped"
            item.undo_error = f"Original path is occupied; not overwritten: {source}"
            manifest.errors.append(item.undo_error)
            manifest.undo_updated_at = iso_timestamp()
            store.save(manifest)
            continue
        if not _exists(destination) or destination.is_symlink():
            item.undo_status = "skipped"
            item.undo_error = f"Organized destination is missing or unsafe: {destination}"
            manifest.errors.append(item.undo_error)
            manifest.undo_updated_at = iso_timestamp()
            store.save(manifest)
            continue

        expected_digest = item.final_sha256 or item.expected_sha256
        expected_snapshot = (
            FileSnapshot.from_dict(item.moved_snapshot)
            if item.moved_snapshot
            else snapshot_path(destination)
        )
        try:
            current_snapshot = snapshot_path(destination)
            if not expected_snapshot.matches(current_snapshot):
                raise FileChangedError(
                    f"Moved file metadata changed after organization: {destination}"
                )
            if hash_file(destination, chunk_size=chunk_size) != expected_digest:
                raise FileChangedError(
                    f"Moved file contents changed after organization: {destination}"
                )
        except OSError as exc:
            item.undo_status = "skipped"
            item.undo_error = str(exc)
            manifest.errors.append(f"Undo {destination}: {exc}")
            manifest.undo_updated_at = iso_timestamp()
            store.save(manifest)
            continue

        # Parent creation is delegated to the safe mover after it validates containment.
        temp_path = source.parent / (
            f".smart-organizer-undo-{run_id[:12]}-{index:05d}-{uuid.uuid4().hex[:8]}.tmp"
        )
        item.undo_temporary_path = str(temp_path)
        item.undo_status = "moving"
        item.undo_error = None
        manifest.undo_updated_at = iso_timestamp()
        store.save(manifest)
        try:
            result = move_file_safely(
                destination,
                source,
                source_root=destination_root,
                destination_root=root,
                expected_snapshot=expected_snapshot,
                expected_sha256=expected_digest,
                temporary_path=temp_path,
                chunk_size=chunk_size,
            )
            item.undo_status = "undone"
            item.undo_error = None
            item.recovery_note = None
            # Keep the digest and original snapshot; the undo operation itself is recorded
            # through undo_status so the original apply manifest remains auditable.
            if result.sha256 != expected_digest:
                item.undo_status = "needs_attention"
                item.undo_error = "Undo copy digest did not match the recorded operation."
        except (OSError, ValueError) as exc:
            item.undo_status = "failed"
            item.undo_error = str(exc)
            manifest.errors.append(f"Undo {destination}: {exc}")
        manifest.undo_updated_at = iso_timestamp()
        store.save(manifest)

    moved_statuses = [item.undo_status for item in manifest.items if item.status == "moved"]
    if moved_statuses and all(status == "undone" for status in moved_statuses):
        manifest.status = "undone"
    elif any(status == "undone" for status in moved_statuses):
        manifest.status = "partially_undone"
    elif any(status in {"failed", "needs_attention", "skipped"} for status in moved_statuses):
        manifest.status = "undo_needs_attention"
    manifest.updated_at = iso_timestamp()
    manifest.metadata["undo_finished_at"] = manifest.updated_at
    manifest.metadata["undo_duration_seconds"] = round(time.monotonic() - started_clock, 6)
    store.save(manifest)
    return manifest
