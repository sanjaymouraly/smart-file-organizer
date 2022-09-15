"""Typed data models shared by the scanner, organizer, CLI, and GUI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _ns(stat_result: Any, attr: str, seconds_attr: str) -> int:
    value = getattr(stat_result, attr, None)
    if value is not None:
        return int(value)
    return int(getattr(stat_result, seconds_attr) * 1_000_000_000)


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """A lightweight identity and metadata snapshot for a regular file."""

    size: int
    mtime_ns: int
    ctime_ns: int
    atime_ns: int
    device: int
    inode: int
    links: int

    @classmethod
    def from_stat(cls, stat_result: Any) -> FileSnapshot:
        return cls(
            size=int(stat_result.st_size),
            mtime_ns=_ns(stat_result, "st_mtime_ns", "st_mtime"),
            ctime_ns=_ns(stat_result, "st_ctime_ns", "st_ctime"),
            atime_ns=_ns(stat_result, "st_atime_ns", "st_atime"),
            device=int(getattr(stat_result, "st_dev", 0)),
            inode=int(getattr(stat_result, "st_ino", 0)),
            links=int(getattr(stat_result, "st_nlink", 1)),
        )

    def matches(self, other: FileSnapshot, *, require_identity: bool = True) -> bool:
        """Return whether the stable fields agree; access time is deliberately ignored.

        On Windows the change-time is not a monotonic stability marker: creating a
        hard link (which the move pipeline performs for every staged file) updates the
        change-time asynchronously, sometimes to an earlier value than a prior read.
        Content integrity is enforced by SHA-256, so size and modification time are
        sufficient pre-filter markers there. On POSIX the change-time is synchronous
        and monotonic, so it remains part of the comparison.
        """
        if (self.size, self.mtime_ns) != (other.size, other.mtime_ns):
            return False
        if os.name != "nt" and self.ctime_ns != other.ctime_ns:
            return False
        if require_identity:
            if self.device and other.device and self.device != other.device:
                return False
            if self.inode and other.inode and self.inode != other.inode:
                return False
        return True

    def to_dict(self) -> dict[str, int]:
        return {
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
            "atime_ns": self.atime_ns,
            "device": self.device,
            "inode": self.inode,
            "links": self.links,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileSnapshot:
        return cls(
            size=int(data["size"]),
            mtime_ns=int(data["mtime_ns"]),
            ctime_ns=int(data.get("ctime_ns", data["mtime_ns"])),
            atime_ns=int(data.get("atime_ns", data["mtime_ns"])),
            device=int(data.get("device", 0)),
            inode=int(data.get("inode", 0)),
            links=int(data.get("links", 1)),
        )


@dataclass(frozen=True, slots=True)
class Classification:
    """A file category plus optional lightweight signature-validation details."""

    category: str
    extension: str
    signature: str | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class FileRecord:
    """A scanned regular file and the metadata captured without modifying it."""

    path: Path
    relative_path: str
    snapshot: FileSnapshot
    extension: str
    category: str
    signature: str | None = None
    warning: str | None = None

    @property
    def size(self) -> int:
        return self.snapshot.size

    @property
    def mtime_ns(self) -> int:
        return self.snapshot.mtime_ns


@dataclass(frozen=True, slots=True)
class ScanIssue:
    """A recoverable filesystem or analysis issue."""

    path: str
    operation: str
    message: str


@dataclass(frozen=True, slots=True)
class ScanProgress:
    """Progress snapshot emitted by a directory scan."""

    files_seen: int
    directories_seen: int
    current_path: str


@dataclass(slots=True)
class ScanResult:
    """The results and counters from a non-mutating directory scan."""

    root: Path
    files: list[FileRecord]
    started_at: str
    finished_at: str
    duration_seconds: float
    directories_scanned: int = 0
    hidden_skipped: int = 0
    excluded_skipped: int = 0
    symlinks_skipped: int = 0
    special_files_skipped: int = 0
    issues: list[ScanIssue] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(record.size for record in self.files)


@dataclass(frozen=True, slots=True)
class DuplicateFile:
    """A file participating in a SHA-256 duplicate group."""

    path: Path
    size: int
    sha256: str
    snapshot: FileSnapshot


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Files with the same size and SHA-256 digest."""

    sha256: str
    size: int
    files: tuple[DuplicateFile, ...]
    default_keep: Path
    reclaimable_bytes: int


@dataclass(slots=True)
class DuplicateAnalysis:
    """Duplicate groups and hashing statistics for one scan."""

    groups: list[DuplicateGroup]
    issues: list[ScanIssue]
    files_hashed: int
    bytes_hashed: int
    duration_seconds: float

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(group.files) for group in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(group.reclaimable_bytes for group in self.groups)


@dataclass(frozen=True, slots=True)
class PlanItem:
    """One safe, previewed source-to-destination move."""

    source: Path
    destination: Path
    snapshot: FileSnapshot
    expected_sha256: str
    category: str
    conflict_note: str | None = None
    reason: str = "organize"


@dataclass(slots=True)
class OrganizationPlan:
    """An immutable-in-practice preview; applying it revalidates each source."""

    root: Path
    destination_root: Path
    items: list[PlanItem]
    generated_at: str
    warnings: list[str] = field(default_factory=list)
    skipped_count: int = 0
    conflict_count: int = 0
    rules: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ManifestItem:
    """Persistent per-file progress and recovery information."""

    source: str
    destination: str
    category: str
    size: int
    original_snapshot: dict[str, int]
    expected_sha256: str
    status: str = "pending"
    error: str | None = None
    moved_snapshot: dict[str, int] | None = None
    final_sha256: str | None = None
    temporary_path: str | None = None
    undo_temporary_path: str | None = None
    conflict_note: str | None = None
    undo_status: str = "not_requested"
    undo_error: str | None = None
    recovery_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "category": self.category,
            "size": self.size,
            "original_snapshot": self.original_snapshot,
            "expected_sha256": self.expected_sha256,
            "status": self.status,
            "error": self.error,
            "moved_snapshot": self.moved_snapshot,
            "final_sha256": self.final_sha256,
            "temporary_path": self.temporary_path,
            "undo_temporary_path": self.undo_temporary_path,
            "conflict_note": self.conflict_note,
            "undo_status": self.undo_status,
            "undo_error": self.undo_error,
            "recovery_note": self.recovery_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestItem:
        return cls(
            source=str(data["source"]),
            destination=str(data["destination"]),
            category=str(data.get("category", "Other")),
            size=int(data.get("size", 0)),
            original_snapshot=dict(data.get("original_snapshot", {})),
            expected_sha256=str(data.get("expected_sha256", "")),
            status=str(data.get("status", "pending")),
            error=data.get("error"),
            moved_snapshot=data.get("moved_snapshot"),
            final_sha256=data.get("final_sha256"),
            temporary_path=data.get("temporary_path"),
            undo_temporary_path=data.get("undo_temporary_path"),
            conflict_note=data.get("conflict_note"),
            undo_status=str(data.get("undo_status", "not_requested")),
            undo_error=data.get("undo_error"),
            recovery_note=data.get("recovery_note"),
        )


@dataclass(slots=True)
class OperationManifest:
    """Durable record for an apply, quarantine, recovery, or undo operation."""

    run_id: str
    kind: str
    created_at: str
    updated_at: str
    status: str
    root: str
    destination_root: str
    items: list[ManifestItem]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    undo_updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "root": self.root,
            "destination_root": self.destination_root,
            "items": [item.to_dict() for item in self.items],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "metadata": self.metadata,
            "undo_updated_at": self.undo_updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationManifest:
        if int(data.get("schema_version", 1)) != 1:
            raise ValueError(f"Unsupported manifest schema version: {data.get('schema_version')}")
        return cls(
            run_id=str(data["run_id"]),
            kind=str(data["kind"]),
            created_at=str(data["created_at"]),
            updated_at=str(data.get("updated_at", data["created_at"])),
            status=str(data.get("status", "unknown")),
            root=str(data["root"]),
            destination_root=str(data["destination_root"]),
            items=[ManifestItem.from_dict(item) for item in data.get("items", [])],
            warnings=list(data.get("warnings", [])),
            errors=list(data.get("errors", [])),
            metadata=dict(data.get("metadata", {})),
            schema_version=int(data.get("schema_version", 1)),
            undo_updated_at=data.get("undo_updated_at"),
        )
