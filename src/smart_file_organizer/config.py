"""Human-readable JSON configuration and platform-appropriate storage locations."""

from __future__ import annotations

import json
import os
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import safe_component


class ConfigError(ValueError):
    """Raised when a configuration file is invalid or unsafe."""


DEFAULT_EXTENSION_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Documents": (".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"),
    "Spreadsheets": (".csv", ".xls", ".xlsx", ".ods"),
    "Images": (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
        ".bmp",
        ".heic",
    ),
    "Presentations": (".ppt", ".pptx", ".odp"),
    "Archives": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"),
    "Videos": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v"),
    "Audio": (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".opus"),
    "Code": (
        ".py",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".sh",
        ".ps1",
        ".java",
        ".c",
        ".cpp",
        ".rs",
        ".go",
    ),
}

DEFAULT_EXCLUDED_DIRS = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".smart-organizer",
    ".smart-organizer-quarantine",
)

_ALLOWED_LAYOUT_FIELDS = {"category", "year", "month", "extension"}
_ALLOWED_FILENAME_FIELDS = {"name", "stem", "suffix", "extension"}


@dataclass(slots=True)
class Config:
    """Validated user preferences shared by CLI and GUI."""

    extension_categories: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_EXTENSION_CATEGORIES)
    )
    include_hidden: bool = False
    excluded_dirs: tuple[str, ...] = DEFAULT_EXCLUDED_DIRS
    excluded_patterns: tuple[str, ...] = ()
    destination_name: str = "Organized"
    destination_layout: str = "{category}"
    filename_template: str = "{name}"
    signature_validation: bool = True
    hash_chunk_size: int = 1024 * 1024
    storage_dir: str | None = None
    config_file: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "storage_dir": self.storage_dir,
            "include_hidden": self.include_hidden,
            "excluded_dirs": list(self.excluded_dirs),
            "excluded_patterns": list(self.excluded_patterns),
            "destination_name": self.destination_name,
            "destination_layout": self.destination_layout,
            "filename_template": self.filename_template,
            "signature_validation": self.signature_validation,
            "hash_chunk_size": self.hash_chunk_size,
            "extensions": {
                category: list(extensions)
                for category, extensions in self.extension_categories.items()
            },
        }


def default_config_path() -> Path:
    """Return the conventional per-user configuration path without creating it."""
    if os.name == "nt":
        value = os.environ.get("APPDATA")
        base = Path(value) if value else Path.home() / "AppData" / "Roaming"
        return base / "SmartFileOrganizer" / "config.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "smart-file-organizer" / "config.json"


def default_storage_dir() -> Path:
    """Return an OS-appropriate directory for operation manifests and quarantine."""
    if os.name == "nt":
        value = os.environ.get("LOCALAPPDATA")
        base = Path(value) if value else Path.home() / "AppData" / "Local"
        return base / "SmartFileOrganizer"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "smart-file-organizer"


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"'{field_name}' must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _validate_template(template: Any, field_name: str, allowed_fields: set[str]) -> str:
    if not isinstance(template, str) or not template.strip():
        raise ConfigError(f"'{field_name}' must be a non-empty string")
    try:
        fields = [
            field_name for _, field_name, _, _ in string.Formatter().parse(template) if field_name
        ]
    except ValueError as exc:
        raise ConfigError(f"'{field_name}' has invalid braces: {exc}") from exc
    for field_name_part in fields:
        if field_name_part not in allowed_fields:
            raise ConfigError(
                f"'{field_name}' uses unsupported placeholder '{{{field_name_part}}}'"
            )
    return template


def _validate_layout(template: Any) -> str:
    value = _validate_template(template, "destination_layout", _ALLOWED_LAYOUT_FIELDS)
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        raise ConfigError("'destination_layout' must be a relative path")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ConfigError("'destination_layout' must not contain empty or traversal components")
    if any("/" in part or "\\" in part for part in parts):
        raise ConfigError("'destination_layout' contains an invalid path component")
    return "/".join(parts)


def _validate_filename_template(template: Any) -> str:
    value = _validate_template(template, "filename_template", _ALLOWED_FILENAME_FIELDS)
    if "/" in value or "\\" in value:
        raise ConfigError("'filename_template' must produce a single filename")
    return value


def _load_extensions(data: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(data, dict) or not data:
        raise ConfigError(
            "'extensions' must be a non-empty object of categories and extension lists"
        )
    result: dict[str, tuple[str, ...]] = {}
    seen: dict[str, str] = {}
    for category, raw_extensions in data.items():
        if not isinstance(category, str) or not category.strip():
            raise ConfigError("Extension category names must be non-empty strings")
        category = category.strip()
        if any(char in category for char in "/\\\x00") or category in {".", ".."}:
            raise ConfigError(f"Unsafe extension category name: {category!r}")
        extensions = _string_list(raw_extensions, f"extensions.{category}")
        normalized: list[str] = []
        for extension in extensions:
            extension = extension.lower()
            if not extension.startswith(".") or len(extension) < 2:
                raise ConfigError(
                    f"Extensions must include a leading dot (invalid value {extension!r})"
                )
            if any(char in extension for char in "/\\\x00"):
                raise ConfigError(f"Invalid extension: {extension!r}")
            previous = seen.get(extension)
            if previous is not None:
                raise ConfigError(
                    f"Extension {extension!r} is assigned to both {previous!r} and {category!r}"
                )
            seen[extension] = category
            normalized.append(extension)
        result[category] = tuple(sorted(set(normalized)))
    return result


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate JSON preferences; absent default config means built-in defaults."""
    config_path = (
        Path(path).expanduser().resolve(strict=False) if path is not None else default_config_path()
    )
    config = Config(config_file=config_path)
    if not config_path.exists():
        if path is not None:
            raise ConfigError(f"Configuration file does not exist: {config_path}")
        return config
    try:
        # encoding="utf-8-sig" transparently accepts a byte-order mark, which
        # Windows editors (Notepad, PowerShell's Set-Content) add by default.
        with config_path.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("The configuration must be a JSON object")
    known_keys = {
        "version",
        "storage_dir",
        "include_hidden",
        "excluded_dirs",
        "excluded_patterns",
        "destination_name",
        "destination_layout",
        "filename_template",
        "signature_validation",
        "hash_chunk_size",
        "extensions",
    }
    unknown = sorted(set(data) - known_keys)
    if unknown:
        raise ConfigError(f"Unknown configuration key(s): {', '.join(unknown)}")
    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ConfigError(f"Unsupported configuration version: {version!r}")

    if "extensions" in data:
        config.extension_categories = _load_extensions(data["extensions"])
    for field_name in ("include_hidden", "signature_validation"):
        if field_name in data:
            value = data[field_name]
            if not isinstance(value, bool):
                raise ConfigError(f"'{field_name}' must be true or false")
            setattr(config, field_name, value)
    if "excluded_dirs" in data:
        config.excluded_dirs = _string_list(data["excluded_dirs"], "excluded_dirs")
    if "excluded_patterns" in data:
        config.excluded_patterns = _string_list(data["excluded_patterns"], "excluded_patterns")
    if "destination_name" in data:
        value = data["destination_name"]
        if not isinstance(value, str) or not value.strip() or value in {".", ".."}:
            raise ConfigError("'destination_name' must be a safe, non-empty directory name")
        if any(char in value for char in "/\\\x00"):
            raise ConfigError("'destination_name' must be a single directory name")
        if safe_component(value.strip()) != value.strip():
            raise ConfigError("'destination_name' must be a portable, safe path component")
        config.destination_name = value.strip()
    if "destination_layout" in data:
        config.destination_layout = _validate_layout(data["destination_layout"])
    if "filename_template" in data:
        config.filename_template = _validate_filename_template(data["filename_template"])
    if "hash_chunk_size" in data:
        value = data["hash_chunk_size"]
        if not isinstance(value, int) or not 4096 <= value <= 16 * 1024 * 1024:
            raise ConfigError("'hash_chunk_size' must be an integer between 4096 and 16777216")
        config.hash_chunk_size = value
    if "storage_dir" in data:
        value = data["storage_dir"]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigError("'storage_dir' must be null or a non-empty path string")
        config.storage_dir = value.strip() if isinstance(value, str) else None
    return config


def resolve_storage_dir(config: Config) -> Path:
    """Resolve configured state storage, with relative paths based on the config file."""
    if not config.storage_dir:
        return default_storage_dir().expanduser().resolve(strict=False)
    path = Path(config.storage_dir).expanduser()
    if not path.is_absolute():
        base = config.config_file.parent if config.config_file else Path.cwd()
        path = base / path
    return path.resolve(strict=False)


def write_default_config(path: str | Path, *, overwrite: bool = False) -> Path:
    """Create a documented starter config without silently replacing user data."""
    destination = Path(path).expanduser().resolve(strict=False)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Configuration already exists: {destination}")
    config = Config(config_file=destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as stream:
        json.dump(config.to_dict(), stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return destination
