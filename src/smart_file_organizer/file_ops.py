"""Safe, reversible file moves implemented as verified copy-then-publish operations."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .models import FileSnapshot
from .utils import (
    FileChangedError,
    FileSafetyError,
    ensure_within,
    fsync_directory,
    hash_file,
    open_regular_readonly,
    snapshot_path,
)


class PartialMoveError(OSError):
    """A destination copy was created but the source could not safely be removed."""

    def __init__(self, message: str, *, destination_created: bool = True) -> None:
        super().__init__(message)
        self.destination_created = destination_created


@dataclass(frozen=True, slots=True)
class MoveResult:
    """Verified result after a source path was moved to its destination."""

    sha256: str
    destination_snapshot: FileSnapshot


def _same_snapshot(expected: FileSnapshot, path: Path) -> bool:
    try:
        current = snapshot_path(path)
    except OSError:
        return False
    return expected.matches(current)


def _unlink_if_same_file(path: Path, identity: tuple[int, int] | None) -> bool:
    """Remove a file only if it still has the device/inode created by this process."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return False
        current = (int(getattr(info, "st_dev", 0)), int(getattr(info, "st_ino", 0)))
        if identity is not None and all(identity) and current != identity:
            return False
        path.unlink()
        return True
    except OSError:
        return False


def _copy_to_temp(
    source: Path,
    temporary: Path,
    *,
    expected_snapshot: FileSnapshot,
    expected_sha256: str,
    chunk_size: int,
) -> tuple[str, tuple[int, int]]:
    """Stream a source into an exclusive temporary file and return digest and identity."""
    source_stream, opened_snapshot = open_regular_readonly(source)
    if not expected_snapshot.matches(opened_snapshot):
        source_stream.close()
        raise FileChangedError(f"Source changed since preview: {source}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        temporary_fd = os.open(temporary, flags, 0o600)
    except OSError:
        source_stream.close()
        raise

    temporary_info = os.fstat(temporary_fd)
    identity = (
        int(getattr(temporary_info, "st_dev", 0)),
        int(getattr(temporary_info, "st_ino", 0)),
    )
    digest = hashlib.sha256()
    try:
        with source_stream, os.fdopen(temporary_fd, "wb") as target:
            while chunk := source_stream.read(chunk_size):
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
            source_after = FileSnapshot.from_stat(os.fstat(source_stream.fileno()))
            if not expected_snapshot.matches(source_after):
                raise FileChangedError(f"Source changed while it was copied: {source}")
        current_path_snapshot = snapshot_path(source)
        if not expected_snapshot.matches(current_path_snapshot):
            raise FileChangedError(f"Source path changed while it was copied: {source}")
        value = digest.hexdigest()
        if value != expected_sha256:
            raise FileChangedError(f"Source contents changed since preview: {source}")
        os.chmod(temporary, stat.S_IMODE(opened_snapshot_mode(source, expected_snapshot)))
        # Preserve the original write time; reading may update access time on some filesystems.
        original_atime = expected_snapshot.atime_ns
        os.utime(temporary, ns=(original_atime, expected_snapshot.mtime_ns))
        fsync_directory(temporary.parent)
        return value, identity
    except BaseException:
        _unlink_if_same_file(temporary, identity)
        raise


def opened_snapshot_mode(source: Path, expected_snapshot: FileSnapshot) -> int:
    """Read the source mode after a snapshot check for cross-filesystem copy metadata."""
    info = source.lstat()
    current = FileSnapshot.from_stat(info)
    if not expected_snapshot.matches(current) or not stat.S_ISREG(info.st_mode):
        raise FileChangedError(f"Source changed before metadata preservation: {source}")
    return info.st_mode


def _publish_temp(temporary: Path, destination: Path) -> None:
    """Atomically publish without replacing an existing name when hard links are supported."""
    try:
        os.link(temporary, destination)
        return
    except FileExistsError:
        raise
    except OSError as link_error:
        # Some filesystems do not support hard links. O_EXCL still prevents overwrite.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            destination_fd = os.open(destination, flags, 0o600)
        except OSError as copy_error:
            if isinstance(copy_error, FileExistsError):
                raise
            raise OSError(
                f"Could not safely publish {destination}: hard-link failed ({link_error}); "
                f"exclusive copy failed ({copy_error})"
            ) from copy_error
        destination_info = os.fstat(destination_fd)
        identity = (
            int(getattr(destination_info, "st_dev", 0)),
            int(getattr(destination_info, "st_ino", 0)),
        )
        try:
            target_stream = os.fdopen(destination_fd, "wb")
            with target_stream as target:
                with temporary.open("rb") as source:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            try:
                shutil.copystat(temporary, destination, follow_symlinks=False)
            except OSError:
                # Content publication remains valid; the original remains until verified.
                pass
        except BaseException as exc:
            # A crash can still leave a partial file. It is intentionally not overwritten or
            # blindly removed; the manifest records this state for manual recovery.
            raise PartialMoveError(
                f"Exclusive destination copy was interrupted at {destination}: {exc}"
            ) from exc
        if not destination.exists():
            raise PartialMoveError(f"Published destination disappeared: {destination}") from None
        current = destination.lstat()
        if (
            identity[0]
            and identity[1]
            and (int(getattr(current, "st_dev", 0)), int(getattr(current, "st_ino", 0))) != identity
        ):
            raise PartialMoveError(
                f"Destination was replaced during publication: {destination}"
            ) from None


def move_file_safely(
    source: Path,
    destination: Path,
    *,
    source_root: Path,
    destination_root: Path,
    expected_snapshot: FileSnapshot,
    expected_sha256: str,
    temporary_path: Path,
    chunk_size: int = 1024 * 1024,
) -> MoveResult:
    """Copy, verify, publish with no-overwrite semantics, then remove the source.

    This is intentionally not described as an atomic batch operation. A power loss
    between steps can leave both copies or a manifest-marked temporary file, but the
    source is not removed until a verified destination exists.
    """
    resolved_source_root = source_root.expanduser().resolve(strict=False)
    resolved_destination_root = destination_root.expanduser().resolve(strict=False)
    source_resolved = ensure_within(source, resolved_source_root, label="source")
    ensure_within(destination, resolved_destination_root, label="destination")
    if source_resolved == destination.resolve(strict=False):
        raise FileSafetyError("Source and destination are the same path")
    if source.is_symlink():
        raise FileSafetyError(f"Symbolic-link sources are not moved: {source}")
    current_snapshot = snapshot_path(source)
    if not expected_snapshot.matches(current_snapshot):
        raise FileChangedError(f"Source changed since preview: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after creating parent directories; an existing symlink must not escape root.
    ensure_within(destination, resolved_destination_root, label="destination")
    ensure_within(temporary_path, resolved_destination_root, label="temporary path")
    if temporary_path.parent.resolve(strict=False) != destination.parent.resolve(strict=False):
        raise FileSafetyError("Temporary file must be beside its destination")
    if os.path.lexists(destination):
        raise FileExistsError(
            errno.EEXIST, "Destination already exists; refusing to overwrite", destination
        )

    digest, temporary_identity = _copy_to_temp(
        source,
        temporary_path,
        expected_snapshot=expected_snapshot,
        expected_sha256=expected_sha256,
        chunk_size=chunk_size,
    )
    destination_created = False
    try:
        _publish_temp(temporary_path, destination)
        destination_created = True
        if hash_file(destination, chunk_size=chunk_size) != expected_sha256:
            raise PartialMoveError(f"Destination content verification failed: {destination}")
        # Remove our staging name before capturing metadata; on filesystems where it is a
        # hard link, unlinking it updates ctime on the final destination.
        if not _unlink_if_same_file(temporary_path, temporary_identity):
            raise PartialMoveError(
                f"Could not safely remove operation temporary file: {temporary_path}"
            )
        temporary_identity = None
        moved_snapshot = snapshot_path(destination)
        try:
            current_source_digest = hash_file(
                source, chunk_size=chunk_size, expected=expected_snapshot
            )
        except OSError as exc:
            raise PartialMoveError(
                f"Destination was created but source could not be revalidated: {exc}"
            ) from exc
        if current_source_digest != expected_sha256:
            raise PartialMoveError(
                f"Destination was created, but source contents changed before removal: {source}"
            )
        # Remove only after destination and source have both been verified.
        if not _same_snapshot(expected_snapshot, source):
            raise PartialMoveError(f"Source changed immediately before removal: {source}")
        source.unlink()
        fsync_directory(source.parent)
        fsync_directory(destination.parent)
        return MoveResult(digest, moved_snapshot)
    except PartialMoveError:
        raise
    except BaseException as exc:
        if destination_created:
            raise PartialMoveError(
                f"Destination was created, but the source was left in place: {exc}"
            ) from exc
        raise
    finally:
        # A published destination has its own name; a leftover managed temp can be removed
        # only when its device/inode still matches the file created by this operation.
        if temporary_identity is not None:
            _unlink_if_same_file(temporary_path, temporary_identity)
