"""Deterministic organization previews and manifest-backed apply operations."""

from __future__ import annotations

import os
import string
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .duplicates import select_retained_copies
from .file_ops import PartialMoveError, move_file_safely
from .history import ManifestStore
from .models import (
    DuplicateAnalysis,
    OrganizationPlan,
    PlanItem,
    ScanResult,
)
from .utils import (
    FileSafetyError,
    collision_key,
    filename_with_number,
    hash_file,
    is_relative_to,
    iso_timestamp,
    safe_component,
    safe_filename,
)

OperationProgress = Callable[[int, int, str, str], None]


def default_destination(root: Path, config: Config) -> Path:
    """Return the conventional destination folder inside the selected directory."""
    return (root / config.destination_name).resolve(strict=False)


def _format_layout(
    record_path: Path,
    category: str,
    mtime_ns: int,
    extension: str,
    config: Config,
    *,
    by_month: bool,
    by_extension: bool,
) -> list[str]:
    timestamp = datetime.fromtimestamp(mtime_ns / 1_000_000_000)
    fields = {
        "category": safe_component(category),
        "year": f"{timestamp.year:04d}",
        "month": f"{timestamp.month:02d}",
        "extension": safe_component(extension.lstrip(".") or "no-extension"),
    }
    try:
        rendered = config.destination_layout.format(**fields)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Could not expand destination_layout: {exc}") from exc
    components = [safe_component(part) for part in rendered.replace("\\", "/").split("/") if part]
    layout_fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(config.destination_layout)
        if field_name
    }
    if by_month and not {"year", "month"}.issubset(layout_fields):
        components.extend((fields["year"], fields["month"]))
    if by_extension and "extension" not in layout_fields:
        components.append(fields["extension"])
    return components


def _render_filename(source: Path, extension: str, config: Config) -> str:
    fields = {
        "name": source.name,
        "stem": source.stem,
        "suffix": source.suffix,
        "extension": extension.lstrip("."),
    }
    try:
        rendered = config.filename_template.format(**fields)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Could not expand filename_template: {exc}") from exc
    if "/" in rendered or "\\" in rendered:
        raise ValueError("filename_template generated a path separator")
    return safe_filename(rendered)


def _exists_with_case_rule(
    path: Path,
    case_sensitive: bool,
    existing_names: dict[str, set[str]],
) -> bool:
    if os.path.lexists(path):
        return True
    if case_sensitive:
        return False
    parent_key = collision_key(path.parent, case_sensitive=False)
    if parent_key not in existing_names:
        try:
            existing_names[parent_key] = {entry.name.casefold() for entry in path.parent.iterdir()}
        except OSError:
            existing_names[parent_key] = set()
    return path.name.casefold() in existing_names[parent_key]


def _choose_destination(
    desired: Path,
    reserved: set[str],
    *,
    case_sensitive: bool | None = None,
    existing_names: dict[str, set[str]] | None = None,
) -> tuple[Path, str | None]:
    if case_sensitive is None:
        case_sensitive = os.name != "nt" and sys.platform != "darwin"
    existing_names = existing_names if existing_names is not None else {}
    key = collision_key(desired, case_sensitive=case_sensitive)
    if key not in reserved and not _exists_with_case_rule(desired, case_sensitive, existing_names):
        reserved.add(key)
        return desired, None
    number = 1
    while True:
        candidate = desired.with_name(filename_with_number(desired.name, number))
        candidate_key = collision_key(candidate, case_sensitive=case_sensitive)
        if candidate_key not in reserved and not _exists_with_case_rule(
            candidate, case_sensitive, existing_names
        ):
            reserved.add(candidate_key)
            return candidate, f"Name conflict at {desired}; assigned {candidate.name}."
        number += 1


def build_organization_plan(
    scan: ScanResult,
    destination_root: str | Path,
    *,
    config: Config | None = None,
    by_month: bool = False,
    by_extension: bool = False,
    protected_paths: tuple[str | Path, ...] = (),
    progress: Callable[[int, int, str], None] | None = None,
    case_sensitive: bool | None = None,
) -> OrganizationPlan:
    """Fingerprint and preview one deterministic category-based organization plan."""
    config = config or Config()
    root = scan.root.resolve(strict=False)
    destination = Path(destination_root).expanduser().resolve(strict=False)
    if destination == root:
        raise FileSafetyError("Destination cannot be the scan directory itself")
    if root.is_relative_to(destination):
        raise FileSafetyError(
            "Destination cannot be an ancestor of the scan directory; choose a separate folder"
        )
    for protected in protected_paths:
        protected_root = Path(protected).expanduser().resolve(strict=False)
        if destination.is_relative_to(protected_root):
            raise FileSafetyError("Destination cannot be inside the application metadata directory")

    items: list[PlanItem] = []
    warnings: list[str] = []
    reserved: set[str] = set()
    existing_names: dict[str, set[str]] = {}
    skipped = 0
    conflict_count = 0
    total = len(scan.files)
    for index, record in enumerate(scan.files, start=1):
        source = record.path
        if is_relative_to(source, destination):
            skipped += 1
            if progress is not None:
                progress(index, total, str(source))
            continue
        if not is_relative_to(source, root):
            warnings.append(f"Skipped source outside selected directory: {source}")
            skipped += 1
            if progress is not None:
                progress(index, total, str(source))
            continue
        try:
            components = _format_layout(
                source,
                record.category,
                record.mtime_ns,
                record.extension,
                config,
                by_month=by_month,
                by_extension=by_extension,
            )
            if not components:
                raise ValueError("Destination layout produced no directory components")
            directory = destination.joinpath(*components)
            ensure_target = directory.resolve(strict=False)
            if not ensure_target.is_relative_to(destination):
                raise FileSafetyError(f"Generated destination escaped its root: {directory}")
            filename = _render_filename(source, record.extension, config)
            desired = ensure_target / filename
            try:
                digest = hash_file(
                    source,
                    chunk_size=config.hash_chunk_size,
                    expected=record.snapshot,
                )
            except OSError as exc:
                warnings.append(f"Could not fingerprint {source}; it will not be moved: {exc}")
                skipped += 1
                if progress is not None:
                    progress(index, total, str(source))
                continue
            actual, conflict_note = _choose_destination(
                desired,
                reserved,
                case_sensitive=case_sensitive,
                existing_names=existing_names,
            )
            if conflict_note:
                conflict_count += 1
                warnings.append(conflict_note)
            items.append(
                PlanItem(
                    source=source,
                    destination=actual,
                    snapshot=record.snapshot,
                    expected_sha256=digest,
                    category=record.category,
                    conflict_note=conflict_note,
                )
            )
        except (OSError, ValueError) as exc:
            warnings.append(f"Could not plan {source}; it will not be moved: {exc}")
            skipped += 1
        if progress is not None:
            progress(index, total, str(source))

    return OrganizationPlan(
        root=root,
        destination_root=destination,
        items=items,
        generated_at=iso_timestamp(),
        warnings=warnings,
        skipped_count=skipped,
        conflict_count=conflict_count,
        rules={
            "category_by_extension": True,
            "destination_layout": config.destination_layout,
            "filename_template": config.filename_template,
            "group_by_month": by_month,
            "group_by_extension": by_extension,
            "signature_validation": config.signature_validation,
        },
    )


def build_quarantine_plan(
    scan: ScanResult,
    analysis: DuplicateAnalysis,
    quarantine_root: str | Path,
    *,
    keep_paths: list[str | Path] | None = None,
    case_sensitive: bool | None = None,
) -> tuple[OrganizationPlan, dict[str, Path]]:
    """Preview reversible quarantine of duplicate copies, keeping one per group."""
    selections = select_retained_copies(analysis.groups, keep_paths)
    categories = {record.path.resolve(strict=False): record.category for record in scan.files}
    root = scan.root.resolve(strict=False)
    quarantine = Path(quarantine_root).expanduser().resolve(strict=False)
    reserved: set[str] = set()
    existing_names: dict[str, set[str]] = {}
    items: list[PlanItem] = []
    warnings: list[str] = []
    conflicts = 0
    for group in analysis.groups:
        retained = selections[group.sha256]
        for duplicate in group.files:
            source = duplicate.path
            if source.resolve(strict=False) == retained:
                continue
            if not is_relative_to(source, root):
                warnings.append(f"Skipped duplicate outside selected directory: {source}")
                continue
            folder = quarantine / group.sha256[:16]
            desired = folder / safe_filename(source.name)
            destination, conflict_note = _choose_destination(
                desired,
                reserved,
                case_sensitive=case_sensitive,
                existing_names=existing_names,
            )
            if conflict_note:
                conflicts += 1
                warnings.append(conflict_note)
            items.append(
                PlanItem(
                    source=source,
                    destination=destination,
                    snapshot=duplicate.snapshot,
                    expected_sha256=group.sha256,
                    category=categories.get(source.resolve(strict=False), "Duplicates"),
                    conflict_note=conflict_note,
                    reason="duplicate quarantine",
                )
            )
    plan = OrganizationPlan(
        root=root,
        destination_root=quarantine,
        items=items,
        generated_at=iso_timestamp(),
        warnings=warnings,
        conflict_count=conflicts,
        rules={
            "operation": "reversible duplicate quarantine",
            "duplicate_groups": len(analysis.groups),
            "duplicate_files": analysis.duplicate_file_count,
            "retained_paths": {digest: str(path) for digest, path in selections.items()},
            "estimated_reclaimable_bytes_if_later_deleted": analysis.reclaimable_bytes,
            "permanent_deletion": False,
        },
    )
    return plan, selections


def _manifest_metadata(plan: OrganizationPlan, metadata: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "plan_generated_at": plan.generated_at,
        "plan_rules": plan.rules,
        "plan_summary": {
            "planned_moves": len(plan.items),
            "skipped_before_apply": plan.skipped_count,
            "conflicts_resolved": plan.conflict_count,
            "warnings": list(plan.warnings),
        },
    }
    if metadata:
        result.update(metadata)
    return result


def apply_plan(
    plan: OrganizationPlan,
    store: ManifestStore,
    *,
    kind: str = "organize",
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    chunk_size: int = 1024 * 1024,
    progress: OperationProgress | None = None,
) -> Any:
    """Apply a previewed plan with per-file revalidation and persistent progress."""
    from .models import ManifestItem, OperationManifest

    started_clock = time.monotonic()
    operation_id = run_id or uuid.uuid4().hex
    if not operation_id.isalnum() or len(operation_id) != 32:
        raise ValueError("run_id must be a 32-character hexadecimal identifier")
    created = iso_timestamp()
    manifest = OperationManifest(
        run_id=operation_id,
        kind=kind,
        created_at=created,
        updated_at=created,
        status="running",
        root=str(plan.root.resolve(strict=False)),
        destination_root=str(plan.destination_root.resolve(strict=False)),
        items=[
            ManifestItem(
                source=str(item.source),
                destination=str(item.destination),
                category=item.category,
                size=item.snapshot.size,
                original_snapshot=item.snapshot.to_dict(),
                expected_sha256=item.expected_sha256,
                conflict_note=item.conflict_note,
            )
            for item in plan.items
        ],
        warnings=list(plan.warnings),
        metadata=_manifest_metadata(plan, metadata),
    )
    store.save(manifest)
    total = len(manifest.items)
    for index, (plan_item, manifest_item) in enumerate(
        zip(plan.items, manifest.items, strict=True), start=1
    ):
        temp_name = f".smart-organizer-{operation_id[:12]}-{index:05d}-{uuid.uuid4().hex[:8]}.tmp"
        temp_path = plan_item.destination.parent / temp_name
        manifest_item.temporary_path = str(temp_path)
        manifest_item.status = "moving"
        manifest.updated_at = iso_timestamp()
        store.save(manifest)
        try:
            result = move_file_safely(
                plan_item.source,
                plan_item.destination,
                source_root=plan.root,
                destination_root=plan.destination_root,
                expected_snapshot=plan_item.snapshot,
                expected_sha256=plan_item.expected_sha256,
                temporary_path=temp_path,
                chunk_size=chunk_size,
            )
            manifest_item.status = "moved"
            manifest_item.final_sha256 = result.sha256
            manifest_item.moved_snapshot = result.destination_snapshot.to_dict()
            manifest_item.error = None
        except PartialMoveError as exc:
            manifest_item.status = "partial"
            manifest_item.error = str(exc)
            manifest.errors.append(f"{plan_item.source}: {exc}")
        except (OSError, ValueError) as exc:
            manifest_item.status = "failed"
            manifest_item.error = str(exc)
            manifest.errors.append(f"{plan_item.source}: {exc}")
        manifest.updated_at = iso_timestamp()
        store.save(manifest)
        if progress is not None:
            progress(index, total, str(plan_item.source), manifest_item.status)

    statuses = [item.status for item in manifest.items]
    succeeded = statuses.count("moved")
    failures = sum(status in {"failed", "partial"} for status in statuses)
    if failures and succeeded:
        manifest.status = "partial"
    elif failures:
        manifest.status = "failed"
    else:
        manifest.status = "completed"
    manifest.updated_at = iso_timestamp()
    manifest.metadata["apply_finished_at"] = manifest.updated_at
    manifest.metadata["apply_duration_seconds"] = round(time.monotonic() - started_clock, 6)
    store.save(manifest)
    return manifest
