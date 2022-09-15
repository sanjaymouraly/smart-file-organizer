from __future__ import annotations

import json
import os
from pathlib import Path, PosixPath
from types import SimpleNamespace

import pytest

from smart_file_organizer.config import (
    Config,
    ConfigError,
    default_config_path,
    default_storage_dir,
    load_config,
    resolve_storage_dir,
)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('{"destination_layout": "{"}', "invalid braces"),
        ('{"destination_layout": "/absolute/{category}"}', "relative path"),
        ('{"destination_layout": "C:/absolute/{category}"}', "relative path"),
        ('{"filename_template": "../{stem}.{extension}"}', "single filename"),
        ('{"filename_template": "{"}', "invalid braces"),
        ('{"filename_template": ""}', "non-empty string"),
        ('{"extensions": {}}', "non-empty object"),
        ('{"extensions": {"../unsafe": [".x"]}}', "Unsafe extension category"),
        ('{"extensions": {"Documents": [".x/y"]}}', "Invalid extension"),
        ('{"destination_name": ""}', "safe, non-empty"),
        ('{"destination_name": "../other"}', "single directory name"),
        ('{"include_hidden": "yes"}', "true or false"),
        ('{"excluded_dirs": "node_modules"}', "list of strings"),
        ('{"storage_dir": "   "}', "non-empty path string"),
        ('{"version": 2}', "Unsupported configuration version"),
        ('{"version": true}', "Unsupported configuration version"),
        ('{"unknown_option": true}', "Unknown configuration key"),
        ("[]", "JSON object"),
    ],
)
def test_config_rejects_malformed_or_unsafe_values(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_config_reports_json_read_errors(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(ConfigError, match="Cannot read configuration"):
        load_config(path)
    path.unlink()
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(path)


def test_config_accepts_utf8_bom_from_windows_editors(tmp_path: Path) -> None:
    """Windows editors such as Notepad and PowerShell's Set-Content add a BOM."""
    path = tmp_path / "bom.json"
    path.write_text(
        json.dumps({"destination_name": "Filed", "include_hidden": True}),
        encoding="utf-8-sig",
    )
    config = load_config(path)
    assert config.destination_name == "Filed"
    assert config.include_hidden is True


def test_relative_storage_path_uses_current_directory_without_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = Config(storage_dir="state", config_file=None)
    assert resolve_storage_dir(config) == (tmp_path / "state").resolve()
    assert config.to_dict()["storage_dir"] == "state"


@pytest.mark.skipif(os.name == "nt", reason="PosixPath cannot be instantiated on Windows")
def test_platform_storage_locations_and_null_config_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.config as config_module

    fake_os = SimpleNamespace(name="posix", environ=os.environ)
    monkeypatch.setattr(config_module, "os", fake_os)
    monkeypatch.setattr(config_module, "Path", PosixPath)
    xdg_config = PosixPath(str(tmp_path / "xdg-config"))
    xdg_state = PosixPath(str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    assert default_config_path() == xdg_config / "smart-file-organizer" / "config.json"
    assert default_storage_dir() == xdg_state / "smart-file-organizer"

    windows_config = PosixPath(str(tmp_path / "roaming"))
    windows_state = PosixPath(str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(windows_config))
    monkeypatch.setenv("LOCALAPPDATA", str(windows_state))
    fake_os.name = "nt"
    assert default_config_path() == windows_config / "SmartFileOrganizer" / "config.json"
    assert default_storage_dir() == windows_state / "SmartFileOrganizer"


def test_null_storage_uses_platform_default_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_file_organizer.config as config_module

    state_dir = tmp_path / "state"
    monkeypatch.setattr(config_module, "default_storage_dir", lambda: state_dir)
    config = Config(storage_dir=None)
    assert resolve_storage_dir(config) == state_dir.resolve()
    assert not state_dir.exists()
