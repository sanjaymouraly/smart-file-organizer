from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from smart_file_organizer.config import (
    Config,
    ConfigError,
    default_config_path,
    load_config,
    resolve_storage_dir,
    write_default_config,
)
from smart_file_organizer.models import OrganizationPlan, PlanItem
from smart_file_organizer.reports import (
    operation_report_data,
    plan_report_data,
    render_report,
    scan_report_data,
    write_report,
)
from smart_file_organizer.scanner import scan_directory


def test_tracked_example_config_loads_as_valid_configuration() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "config.json"
    config = load_config(example)
    assert config.destination_name == "Organized"
    assert config.storage_dir is None
    assert config.extension_categories["Images"]


def test_config_defaults_and_storage_path_resolution(tmp_path: Path) -> None:
    path = tmp_path / "config" / "prefs.json"
    path.parent.mkdir(parents=True)
    write_default_config(path)
    config = load_config(path)
    assert config.destination_name == "Organized"
    assert config.signature_validation is True
    config.storage_dir = "state"
    config.config_file = path
    assert resolve_storage_dir(config) == path.parent / "state"
    assert default_config_path().name == "config.json"


def test_config_init_is_no_clobber_and_valid_json(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "prefs.json")
    loaded = load_config(path)
    assert ".pdf" in loaded.extension_categories["Documents"]
    with pytest.raises(FileExistsError):
        write_default_config(path)
    write_default_config(path, overwrite=True)


def test_config_rejects_invalid_extensions_and_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"extensions": {"Docs": ["pdf"]}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="leading dot"):
        load_config(path)
    path.write_text(json.dumps({"destination_layout": "{category}/../../escape"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="traversal"):
        load_config(path)
    path.write_text(json.dumps({"extensions": {"A": [".x"], "B": [".X"]}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="both"):
        load_config(path)


def test_config_rejects_missing_explicit_file_and_reserved_destination(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "missing.json")
    path = tmp_path / "reserved.json"
    path.write_text(json.dumps({"destination_name": "CON"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="portable"):
        load_config(path)


def test_config_rejects_unknown_placeholder_and_invalid_chunk_size(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"destination_layout": "{category}/{unknown}"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported placeholder"):
        load_config(path)
    path.write_text(json.dumps({"hash_chunk_size": 1}), encoding="utf-8")
    with pytest.raises(ConfigError, match="hash_chunk_size"):
        load_config(path)


def test_scan_report_json_csv_and_html_are_consistent(
    sample_tree: Path, config: Config, tmp_path: Path
) -> None:
    scan = scan_directory(sample_tree, config=config)
    data = scan_report_data(scan)
    assert data["files_scanned"] == len(scan.files)
    assert data["duplicate_analysis"] is None
    encoded = json.loads(render_report(data, "json"))
    assert encoded["selected_directory"] == str(scan.root)
    csv_text = render_report(data, "csv")
    rows = list(csv.DictReader(csv_text.splitlines()))
    assert len(rows) == len(scan.files)
    html_text = render_report(data, "html")
    assert "<!doctype html>" in html_text
    assert "Smart File Organizer" in html_text
    path = write_report(data, tmp_path / "report.html")
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_csv_exports_are_safe_for_spreadsheet_opening_and_do_not_overwrite(
    tmp_path: Path,
) -> None:
    data = {
        "report_type": "scan",
        "files": [
            {
                "path": "/tmp/=SUM(A1:A2)",
                "relative_path": "=SUM(A1:A2)",
                "size_bytes": 1,
                "extension": ".txt",
                "category": "+code",
                "modified_ns": 0,
                "signature": None,
                "warning": None,
                "duplicate_group": None,
            }
        ],
    }
    csv_text = render_report(data, "csv")
    assert "'=SUM(A1:A2)" in csv_text
    assert "'+code" in csv_text
    path = write_report(data, tmp_path / "report.csv")
    with pytest.raises(FileExistsError):
        write_report(data, path)


def test_plan_report_marks_actions_as_not_moved(sample_tree: Path, config: Config) -> None:
    scan = scan_directory(sample_tree, config=config)
    path = scan.files[0]
    plan = OrganizationPlan(
        root=scan.root,
        destination_root=scan.root / "Organized",
        generated_at="2022-01-01T00:00:00+00:00",
        items=[
            PlanItem(
                source=path.path,
                destination=scan.root / "Organized" / path.category / path.path.name,
                snapshot=path.snapshot,
                expected_sha256="digest",
                category=path.category,
            )
        ],
    )
    data = plan_report_data(scan, plan)
    assert data["files_moved"] == 0
    assert data["planned_files"] == 1
    assert any(row["status"] == "planned_not_moved" for row in data["files"])
    assert "no files moved" in render_report(data, "html").lower()
    assert "proposed_destination" in render_report(data, "csv")


def test_operation_report_counts_only_completed_moves(sample_tree: Path, config: Config) -> None:
    from smart_file_organizer.history import ManifestStore
    from smart_file_organizer.organizer import apply_plan, build_organization_plan

    scan = scan_directory(sample_tree, config=config)
    plan = build_organization_plan(scan, sample_tree / "Organized", config=config)
    manifest = apply_plan(
        plan, ManifestStore(tmp_path := Path(config.storage_dir)), chunk_size=4096
    )
    report = operation_report_data(manifest)
    assert report["files_moved"] == len(plan.items)
    assert report["files_failed_or_partial"] == 0
    assert report["operation_id"] == manifest.run_id
    assert report["bytes_moved"] == sum(item.snapshot.size for item in plan.items)
    assert tmp_path.exists()
