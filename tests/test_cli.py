from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_file_organizer.cli import main
from smart_file_organizer.config import write_default_config


def _config_file(tmp_path: Path) -> Path:
    path = write_default_config(tmp_path / "config.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["storage_dir"] = str(tmp_path / "state")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_python_module_entrypoint_exits_with_cli_status(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    import smart_file_organizer.cli as cli_module

    monkeypatch.setattr(cli_module, "main", lambda: 0)
    with pytest.raises(SystemExit) as outcome:
        runpy.run_module("smart_file_organizer.__main__", run_name="__main__")
    assert outcome.value.code == 0


def test_cli_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    assert "preview" in capsys.readouterr().out
    with pytest.raises(SystemExit) as version_exit:
        main(["--version"])
    assert version_exit.value.code == 0
    assert "smart-organizer" in capsys.readouterr().out


def test_cli_scan_and_preview_are_non_destructive(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_file(tmp_path)
    before = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    assert main(["scan", str(sample_tree), "--config", str(config)]) == 0
    assert "Files:" in capsys.readouterr().out
    assert main(["preview", str(sample_tree), "--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "DRY RUN" in output
    assert "->" in output
    after = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_cli_apply_history_report_and_undo_workflow(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_file(tmp_path)
    assert main(["--config", str(config), "organize", str(sample_tree), "--apply"]) == 0
    output = capsys.readouterr().out
    run_id = next(
        line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("Operation ID:")
    )
    assert main(["history", "--config", str(config)]) == 0
    assert run_id in capsys.readouterr().out
    report_path = tmp_path / "operation.html"
    assert (
        main(
            [
                "report",
                run_id,
                "--config",
                str(config),
                "--format",
                "html",
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    assert "Operation report" in report_path.read_text(encoding="utf-8")
    assert main(["undo", run_id, "--config", str(config)]) == 0
    assert "restored" in capsys.readouterr().out


def test_cli_duplicates_reports_without_modifying_and_preview_quarantine(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_file(tmp_path)
    before = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    report_path = tmp_path / "duplicates.csv"
    assert (
        main(
            [
                "duplicates",
                str(sample_tree),
                "--config",
                str(config),
                "--report",
                str(report_path),
                "--format",
                "csv",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Duplicate groups: 2" in output
    assert report_path.read_text(encoding="utf-8").startswith("path,")
    assert main(["duplicates", str(sample_tree), "--config", str(config), "--quarantine"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    after = {
        path.relative_to(sample_tree): path.read_bytes()
        for path in sample_tree.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_cli_requires_quarantine_for_duplicate_apply(sample_tree: Path, tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    assert main(["duplicates", str(sample_tree), "--config", str(config), "--apply"]) == 2


def test_cli_quarantine_apply_and_undo(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_file(tmp_path)
    assert (
        main(["duplicates", str(sample_tree), "--config", str(config), "--quarantine", "--apply"])
        == 0
    )
    output = capsys.readouterr().out
    run_id = next(
        line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("Operation ID:")
    )
    assert not (sample_tree / "notes.txt").exists() or not (sample_tree / "notes copy.txt").exists()
    assert main(["undo", run_id, "--config", str(config)]) == 0
    assert (sample_tree / "notes.txt").exists()
    assert (sample_tree / "notes copy.txt").exists()


def test_cli_missing_path_and_invalid_configuration_return_helpful_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config_file(tmp_path)
    assert main(["scan", str(tmp_path / "missing"), "--config", str(config)]) == 2
    assert "Error:" in capsys.readouterr().err
    invalid = tmp_path / "bad.json"
    invalid.write_text("{not json", encoding="utf-8")
    assert main(["scan", str(tmp_path), "--config", str(invalid)]) == 2
    assert "Cannot read configuration" in capsys.readouterr().err


def test_cli_demo_refuses_nonempty_target(tmp_path: Path) -> None:
    path = tmp_path / "demo"
    path.mkdir()
    (path / "personal.txt").write_text("keep", encoding="utf-8")
    assert main(["demo", str(path)]) == 2
    assert (path / "personal.txt").read_text(encoding="utf-8") == "keep"


def test_cli_config_show_and_init(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "preferences.json"
    assert main(["config", "init", "--config", str(path)]) == 0
    assert "Configuration written" in capsys.readouterr().out
    assert main(["config", "show", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["destination_name"] == "Organized"
    assert main(["scan", str(tmp_path), "--config", str(tmp_path / "missing.json")]) == 2
    assert "does not exist" in capsys.readouterr().err
