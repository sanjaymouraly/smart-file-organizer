"""JSON, CSV, and self-contained HTML reports for scans and operations."""

from __future__ import annotations

import csv
import html
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .models import DuplicateAnalysis, OperationManifest, ScanResult
from .utils import format_bytes


def _category_summary(scan: ScanResult) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    for record in scan.files:
        summary[record.category]["files"] += 1
        summary[record.category]["bytes"] += record.size
    return {key: summary[key] for key in sorted(summary, key=str.casefold)}


def scan_report_data(
    scan: ScanResult, duplicate_analysis: DuplicateAnalysis | None = None
) -> dict[str, Any]:
    """Build a truthful scan report; absent duplicate analysis is not shown as zero."""
    duplicate_paths: dict[str, int] = {}
    duplicate_groups: list[dict[str, Any]] = []
    if duplicate_analysis is not None:
        for index, group in enumerate(duplicate_analysis.groups, start=1):
            duplicate_groups.append(
                {
                    "group": index,
                    "sha256": group.sha256,
                    "size_bytes": group.size,
                    "file_count": len(group.files),
                    "default_keep": str(group.default_keep),
                    "estimated_reclaimable_bytes": group.reclaimable_bytes,
                    "paths": [str(item.path) for item in group.files],
                }
            )
            for item in group.files:
                duplicate_paths[str(item.path)] = index
    files = []
    for record in scan.files:
        files.append(
            {
                "path": str(record.path),
                "relative_path": record.relative_path,
                "size_bytes": record.size,
                "extension": record.extension,
                "category": record.category,
                "modified_ns": record.mtime_ns,
                "signature": record.signature,
                "warning": record.warning,
                "duplicate_group": duplicate_paths.get(str(record.path)),
            }
        )
    data: dict[str, Any] = {
        "report_type": "scan",
        "scan_started_at": scan.started_at,
        "scan_finished_at": scan.finished_at,
        "selected_directory": str(scan.root),
        "files_scanned": len(scan.files),
        "successfully_processed": len(scan.files),
        "total_size_bytes": scan.total_bytes,
        "total_size_human": format_bytes(scan.total_bytes),
        "directories_scanned": scan.directories_scanned,
        "duration_seconds": round(scan.duration_seconds, 6),
        "category_summary": _category_summary(scan),
        "duplicate_analysis": None,
        "warnings": [
            {"path": issue.path, "operation": issue.operation, "message": issue.message}
            for issue in scan.issues
        ],
        "scanner_skips": {
            "hidden": scan.hidden_skipped,
            "excluded": scan.excluded_skipped,
            "symlinks": scan.symlinks_skipped,
            "special_files": scan.special_files_skipped,
        },
        "files": files,
    }
    if duplicate_analysis is not None:
        data["duplicate_analysis"] = {
            "status": "completed",
            "group_count": len(duplicate_analysis.groups),
            "duplicate_file_count": duplicate_analysis.duplicate_file_count,
            "estimated_reclaimable_bytes": duplicate_analysis.reclaimable_bytes,
            "estimated_reclaimable_human": format_bytes(duplicate_analysis.reclaimable_bytes),
            "files_hashed": duplicate_analysis.files_hashed,
            "bytes_hashed": duplicate_analysis.bytes_hashed,
            "duration_seconds": round(duplicate_analysis.duration_seconds, 6),
            "groups": duplicate_groups,
            "warnings": [
                {"path": issue.path, "operation": issue.operation, "message": issue.message}
                for issue in duplicate_analysis.issues
            ],
        }
    return data


def plan_report_data(scan: ScanResult, plan: Any) -> dict[str, Any]:
    """Build a preview report that clearly labels planned moves as not completed."""
    proposed = {str(item.source): item for item in plan.items}
    rows = []
    for record in scan.files:
        item = proposed.get(str(record.path))
        rows.append(
            {
                "path": str(record.path),
                "relative_path": record.relative_path,
                "size_bytes": record.size,
                "extension": record.extension,
                "category": record.category,
                "modified_ns": record.mtime_ns,
                "warning": record.warning,
                "proposed_destination": str(item.destination) if item else None,
                "status": "planned_not_moved" if item else "skipped_or_unchanged",
            }
        )
    return {
        "report_type": "plan",
        "plan_generated_at": plan.generated_at,
        "selected_directory": str(scan.root),
        "destination_directory": str(plan.destination_root),
        "files_scanned": len(scan.files),
        "planned_files": len(plan.items),
        "files_moved": 0,
        "files_skipped": plan.skipped_count,
        "filename_conflicts": plan.conflict_count,
        "total_size_bytes": scan.total_bytes,
        "total_size_human": format_bytes(scan.total_bytes),
        "duration_seconds": round(scan.duration_seconds, 6),
        "organization_rules": plan.rules,
        "warnings": list(plan.warnings)
        + [f"[{issue.operation}] {issue.path}: {issue.message}" for issue in scan.issues],
        "files": rows,
    }


def operation_report_data(
    manifest: OperationManifest, *, manifest_path: str | None = None
) -> dict[str, Any]:
    """Summarize a persisted operation without conflating plans with completed moves."""
    plan_summary = manifest.metadata.get("plan_summary", {})
    moved = [item for item in manifest.items if item.status == "moved"]
    failed = [
        item for item in manifest.items if item.status in {"failed", "partial", "interrupted"}
    ]
    skipped = [item for item in manifest.items if item.status == "skipped"]
    moved_bytes = sum(item.size for item in moved)
    files = [
        {
            "source": item.source,
            "destination": item.destination,
            "category": item.category,
            "size_bytes": item.size,
            "status": item.status,
            "error": item.error,
            "undo_status": item.undo_status,
            "conflict_note": item.conflict_note,
        }
        for item in manifest.items
    ]
    return {
        "report_type": "operation",
        "operation_id": manifest.run_id,
        "operation_kind": manifest.kind,
        "status": manifest.status,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "undo_updated_at": manifest.undo_updated_at,
        "selected_directory": manifest.root,
        "destination_directory": manifest.destination_root,
        "manifest_location": manifest_path,
        "planned_files": len(manifest.items),
        "files_moved": len(moved),
        "files_failed_or_partial": len(failed),
        "files_skipped": len(skipped),
        "files_unchanged_before_apply": int(plan_summary.get("skipped_before_apply", 0)),
        "bytes_moved": moved_bytes,
        "bytes_moved_human": format_bytes(moved_bytes),
        "duration_seconds": manifest.metadata.get("apply_duration_seconds"),
        "undo_duration_seconds": manifest.metadata.get("undo_duration_seconds"),
        "filename_conflicts": int(plan_summary.get("conflicts_resolved", 0)),
        "duplicate_group_count": manifest.metadata.get("duplicate_group_count"),
        "duplicate_file_count": manifest.metadata.get("duplicate_file_count"),
        "estimated_reclaimable_bytes": manifest.metadata.get("estimated_reclaimable_bytes"),
        "organization_rules": manifest.metadata.get("plan_rules", {}),
        "duration_note": "Wall-clock operation time includes per-file safety checks and manifest writes.",
        "warnings": list(manifest.warnings),
        "errors": list(manifest.errors),
        "files": files,
    }


def _rows_for_csv(data: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    if data.get("report_type") == "operation":
        fields = [
            "source",
            "destination",
            "category",
            "size_bytes",
            "status",
            "undo_status",
            "error",
            "conflict_note",
        ]
        return fields, list(data.get("files", []))
    if data.get("report_type") == "plan":
        fields = [
            "path",
            "relative_path",
            "size_bytes",
            "extension",
            "category",
            "proposed_destination",
            "status",
            "warning",
        ]
        return fields, list(data.get("files", []))
    fields = [
        "path",
        "relative_path",
        "size_bytes",
        "extension",
        "category",
        "modified_ns",
        "signature",
        "warning",
        "duplicate_group",
    ]
    return fields, list(data.get("files", []))


def _csv_safe_value(value: Any) -> Any:
    """Prefix spreadsheet formula-like text so exported CSV opens as inert text."""
    if isinstance(value, str) and re.match(r"^\s*[=+@-]", value):
        return "'" + value
    return value


def render_report(data: dict[str, Any], format_name: str = "json") -> str:
    """Render report data as JSON, CSV, or a self-contained HTML document."""
    format_name = format_name.lower()
    if format_name == "json":
        return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if format_name == "csv":
        fields, rows = _rows_for_csv(data)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {key: _csv_safe_value(value) for key, value in row.items()} for row in rows
        )
        return output.getvalue()
    if format_name != "html":
        raise ValueError("Report format must be json, csv, or html")

    title = {
        "scan": "Scan report",
        "plan": "Organization preview report — no files moved",
        "operation": "Operation report",
    }.get(data.get("report_type"), "Smart File Organizer report")
    summary_keys = (
        "operation_id",
        "operation_kind",
        "status",
        "selected_directory",
        "destination_directory",
        "files_scanned",
        "planned_files",
        "files_moved",
        "files_failed_or_partial",
        "files_unchanged_before_apply",
        "total_size_human",
        "bytes_moved_human",
        "filename_conflicts",
        "duration_seconds",
        "manifest_location",
    )
    summary = [
        (key.replace("_", " ").title(), data[key])
        for key in summary_keys
        if data.get(key) is not None
    ]
    if data.get("report_type") == "scan":
        summary.extend(
            (
                ("Files scanned", data.get("files_scanned", 0)),
                ("Total size", data.get("total_size_human", "0 B")),
                ("Directories scanned", data.get("directories_scanned", 0)),
            )
        )
        if data.get("duplicate_analysis") is not None:
            duplicate = data["duplicate_analysis"]
            summary.extend(
                (
                    ("Duplicate groups", duplicate.get("group_count", 0)),
                    ("Estimated reclaimable", duplicate.get("estimated_reclaimable_human", "0 B")),
                )
            )
    summary_html = "".join(
        f"<tr><th>{html.escape(str(label))}</th><td>{html.escape(str(value))}</td></tr>"
        for label, value in summary
    )
    rows = data.get("files", [])
    if data.get("report_type") == "operation":
        columns = ("source", "destination", "category", "size_bytes", "status", "error")
    elif data.get("report_type") == "plan":
        columns = ("path", "proposed_destination", "size_bytes", "category", "status", "warning")
    else:
        columns = ("path", "size_bytes", "extension", "category", "duplicate_group", "warning")
    table_head = "".join(
        f"<th>{html.escape(column.replace('_', ' ').title())}</th>" for column in columns
    )
    table_rows = []
    for row in rows:
        cells = "".join(
            f"<td>{html.escape(str(row.get(column, '') or ''))}</td>" for column in columns
        )
        table_rows.append(f"<tr>{cells}</tr>")
    table_body = "\n".join(table_rows) or f'<tr><td colspan="{len(columns)}">No files</td></tr>'
    warning_values = data.get("warnings", []) + data.get("errors", [])
    warning_items = (
        "".join(
            f"<li>{html.escape(str(item.get('message', item) if isinstance(item, dict) else item))}</li>"
            for item in warning_values
        )
        or "<li>None</li>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }}
body {{ max-width: 1180px; margin: 2rem auto; padding: 0 1rem; color: #172033; background: #f5f7fb; }}
header {{ background: #15264a; color: white; padding: 1.5rem 1.8rem; border-radius: 14px; }}
h1 {{ margin: 0 0 .3rem; }}
.card {{ background: white; padding: 1rem 1.3rem; margin: 1rem 0; border: 1px solid #dbe2ef; border-radius: 12px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }}
th, td {{ text-align: left; padding: .55rem .7rem; border-bottom: 1px solid #e6ebf3; vertical-align: top; }}
th {{ color: #33456b; background: #f0f4fa; }}
td {{ overflow-wrap: anywhere; }}
small {{ color: #d8e2ff; }}
</style>
</head>
<body>
<header><h1>{html.escape(title)}</h1><small>Generated from Smart File Organizer manifest or scan data</small></header>
<section class="card"><h2>Summary</h2><table><tbody>{summary_html}</tbody></table></section>
<section class="card"><h2>Files</h2><table><thead><tr>{table_head}</tr></thead><tbody>{table_body}</tbody></table></section>
<section class="card"><h2>Warnings and errors</h2><ul>{warning_items}</ul></section>
</body>
</html>
"""


def write_report(data: dict[str, Any], path: str | Path, format_name: str | None = None) -> Path:
    """Write a report, selecting the format from the suffix when not specified."""
    destination = Path(path).expanduser()
    selected_format = (format_name or destination.suffix.lstrip(".") or "json").lower()
    if selected_format == "htm":
        selected_format = "html"
    rendered = render_report(data, selected_format)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is deliberate: exporting must not silently overwrite an existing file.
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
    return destination
