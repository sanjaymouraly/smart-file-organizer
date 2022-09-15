"""Small, security-conscious filesystem and formatting helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from .models import FileSnapshot


class FileSafetyError(OSError):
    """Raised when a file cannot be read or validated safely."""


class FileChangedError(FileSafetyError):
    """Raised when a file no longer matches its captured snapshot."""


def iso_timestamp() -> str:
    """Return a timezone-aware, sortable UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def snapshot_path(path: Path, *, follow_symlinks: bool = False) -> FileSnapshot:
    """Capture a file snapshot without following symlinks by default."""
    info = path.stat() if follow_symlinks else path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise FileSafetyError(f"Not a regular file: {path}")
    return FileSnapshot.from_stat(info)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    left_dev = int(getattr(left, "st_dev", 0))
    right_dev = int(getattr(right, "st_dev", 0))
    left_ino = int(getattr(left, "st_ino", 0))
    right_ino = int(getattr(right, "st_ino", 0))
    return (not left_dev or not right_dev or left_dev == right_dev) and (
        not left_ino or not right_ino or left_ino == right_ino
    )


def open_regular_readonly(path: Path) -> tuple[BinaryIO, FileSnapshot]:
    """Open a regular file without following symlinks where the OS supports it."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FileSafetyError(f"Cannot open file safely: {path}: {exc}") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise FileSafetyError(f"Not a regular file: {path}")
        try:
            path_stat = path.lstat()
        except OSError as exc:
            raise FileSafetyError(f"Cannot verify file path: {path}: {exc}") from exc
        if not stat.S_ISREG(path_stat.st_mode) or not _same_identity(opened_stat, path_stat):
            raise FileChangedError(f"File was replaced while opening: {path}")
        return os.fdopen(descriptor, "rb"), FileSnapshot.from_stat(opened_stat)
    except BaseException:
        os.close(descriptor)
        raise


def _snapshot_matches_stat(snapshot: FileSnapshot, info: os.stat_result) -> bool:
    current = FileSnapshot.from_stat(info)
    return snapshot.matches(current)


def hash_file(
    path: Path, *, chunk_size: int = 1024 * 1024, expected: FileSnapshot | None = None
) -> str:
    """Compute SHA-256 using bounded memory and reject files changed during the read."""
    if chunk_size < 4096:
        raise ValueError("chunk_size must be at least 4096 bytes")
    stream, before = open_regular_readonly(path)
    with stream:
        if expected is not None and not expected.matches(before):
            raise FileChangedError(f"File metadata changed since it was scanned: {path}")
        digest = hashlib.sha256()
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
        after_fd = os.fstat(stream.fileno())
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise FileChangedError(f"File disappeared while hashing: {path}") from exc
        if not _snapshot_matches_stat(before, after_fd) or not _snapshot_matches_stat(
            before, after_path
        ):
            raise FileChangedError(f"File changed while hashing: {path}")
        return digest.hexdigest()


def read_file_header(path: Path, *, length: int = 512) -> bytes:
    """Read a small prefix without following symlinks or loading file contents in full."""
    stream, before = open_regular_readonly(path)
    with stream:
        header = stream.read(length)
        after_fd = os.fstat(stream.fileno())
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise FileChangedError(f"File disappeared while reading its signature: {path}") from exc
        if not _snapshot_matches_stat(before, after_fd) or not _snapshot_matches_stat(
            before, after_path
        ):
            raise FileChangedError(f"File changed while reading its signature: {path}")
        return header


def is_relative_to(path: Path, parent: Path) -> bool:
    """Check path containment after resolving existing symlinks."""
    try:
        return path.resolve(strict=False).is_relative_to(parent.resolve(strict=False))
    except (OSError, RuntimeError):
        return False


def ensure_within(path: Path, root: Path, *, label: str = "path") -> Path:
    """Resolve a path and raise if it escapes the supplied root."""
    resolved_root = root.expanduser().resolve(strict=False)
    resolved_path = path.expanduser().resolve(strict=False)
    if not resolved_path.is_relative_to(resolved_root):
        raise FileSafetyError(f"{label} escapes its permitted directory: {path}")
    return resolved_path


def collision_key(path: Path, *, case_sensitive: bool | None = None) -> str:
    """Return a platform-aware path key for deterministic plan conflict detection."""
    text = os.path.normpath(os.fspath(path))
    if case_sensitive is None:
        # macOS volumes are commonly case-insensitive; conservative suffixing avoids races.
        case_sensitive = os.name != "nt" and sys.platform != "darwin"
    return text if case_sensitive else text.casefold()


_INVALID_COMPONENT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def safe_component(value: str, *, max_bytes: int = 120) -> str:
    """Sanitize one path component for portable, non-traversing output paths."""
    cleaned = _INVALID_COMPONENT_CHARS.sub("_", str(value)).strip().rstrip(" .")
    if cleaned in {"", ".", ".."}:
        cleaned = "_"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    encoded = cleaned.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        clipped = encoded[:max_bytes]
        while clipped:
            try:
                cleaned = clipped.decode("utf-8").rstrip(" .")
                break
            except UnicodeDecodeError:
                clipped = clipped[:-1]
        if not cleaned:
            cleaned = "_"
    return cleaned


def safe_filename(value: str, *, max_bytes: int = 240) -> str:
    """Sanitize a filename and preserve its final suffix when shortening it."""
    value = str(value)
    if value in {"", ".", ".."}:
        value = "unnamed"
    value = _INVALID_COMPONENT_CHARS.sub("_", value).strip().rstrip(" .") or "unnamed"
    path = Path(value)
    stem = path.stem.rstrip(" .")
    suffix = path.suffix
    if stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    suffix_bytes = suffix.encode("utf-8", errors="replace")
    available = max(1, max_bytes - len(suffix_bytes))
    stem_bytes = stem.encode("utf-8", errors="replace")[:available]
    while stem_bytes:
        try:
            stem = stem_bytes.decode("utf-8").rstrip(" .")
            break
        except UnicodeDecodeError:
            stem_bytes = stem_bytes[:-1]
    stem = stem or "_"
    return f"{stem}{suffix}"


def filename_with_number(filename: str, number: int) -> str:
    """Add a stable numeric disambiguator before the final extension."""
    path = Path(filename)
    stem = path.stem or "unnamed"
    suffix = path.suffix
    return safe_filename(f"{stem} ({number}){suffix}")


def format_bytes(value: int) -> str:
    """Format bytes using human-readable binary units."""
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024.0 or unit == "PiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically replace a JSON file in the same directory, best-effort fsync included."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def fsync_directory(path: Path) -> None:
    """Request directory-entry durability on POSIX; safely ignore unsupported platforms."""
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def safe_json_load(path: Path) -> dict[str, Any]:
    """Load a JSON object and reject other top-level values."""
    # utf-8-sig also accepts a leading byte-order mark, common in Windows editors.
    with path.open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("JSON document must contain an object at its top level")
    return data
