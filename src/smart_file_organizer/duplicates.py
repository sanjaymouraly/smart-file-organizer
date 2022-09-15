"""Memory-efficient, SHA-256 duplicate detection with hard-link-aware estimates."""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .models import (
    DuplicateAnalysis,
    DuplicateFile,
    DuplicateGroup,
    FileRecord,
    ScanIssue,
)
from .utils import FileChangedError, FileSafetyError, hash_file, snapshot_path


def _inode_key(file: DuplicateFile) -> tuple[int, int] | tuple[str, str]:
    snapshot = file.snapshot
    if snapshot.device or snapshot.inode:
        return snapshot.device, snapshot.inode
    return "path", str(file.path)


def estimate_reclaimable_bytes(files: tuple[DuplicateFile, ...], keep_path: Path) -> int:
    """Estimate physical bytes freed if all non-kept paths were removed.

    Files sharing an inode are counted once. An inode with hard links outside the
    duplicate group is not counted as reclaimable because removing the group's
    names would not release its storage.
    """
    inode_counts: Counter[tuple[int, int] | tuple[str, str]] = Counter(
        _inode_key(file) for file in files
    )
    inode_sizes: dict[tuple[int, int] | tuple[str, str], int] = {}
    inode_links: dict[tuple[int, int] | tuple[str, str], int] = {}
    keep_file = next((item for item in files if item.path == keep_path), files[0])
    keep_inode = _inode_key(keep_file)
    for item in files:
        key = _inode_key(item)
        inode_sizes[key] = max(inode_sizes.get(key, 0), item.size)
        inode_links[key] = max(inode_links.get(key, 1), item.snapshot.links)
    reclaimable = 0
    for key, known_paths in inode_counts.items():
        if key == keep_inode:
            continue
        if inode_links[key] <= known_paths:
            reclaimable += inode_sizes[key]
    return reclaimable


def find_duplicates(
    records: list[FileRecord],
    *,
    config: Config | None = None,
    chunk_size: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> DuplicateAnalysis:
    """Group exact duplicates by size and streaming SHA-256.

    Only files sharing a size are hashed. Symlinks are already excluded by the
    scanner; snapshots are verified before and after each content read.
    """
    config = config or Config()
    chunk_size = config.hash_chunk_size if chunk_size is None else chunk_size
    if chunk_size < 4096:
        raise ValueError("chunk_size must be at least 4096 bytes")
    started = time.monotonic()
    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for record in records:
        by_size[record.size].append(record)
    candidates = [
        record
        for same_size in by_size.values()
        if len(same_size) > 1
        for record in sorted(
            same_size, key=lambda item: (str(item.path).casefold(), str(item.path))
        )
    ]
    digests: dict[tuple[int, int, int, int], str] = {}
    digest_groups: dict[tuple[int, str], list[DuplicateFile]] = defaultdict(list)
    issues: list[ScanIssue] = []
    bytes_hashed = 0
    files_hashed = 0
    total = len(candidates)

    for index, record in enumerate(candidates, start=1):
        snapshot = record.snapshot
        cache_key = (
            (snapshot.device, snapshot.inode, snapshot.size, snapshot.mtime_ns)
            if snapshot.device or snapshot.inode
            else None
        )
        try:
            digest = digests.get(cache_key) if cache_key is not None else None
            if digest is None:
                digest = hash_file(record.path, chunk_size=chunk_size, expected=snapshot)
                if cache_key is not None:
                    digests[cache_key] = digest
                bytes_hashed += record.size
                files_hashed += 1
            elif not snapshot.matches(snapshot_path(record.path)):
                raise FileChangedError(
                    f"Hard-linked file changed since it was scanned: {record.path}"
                )
            digest_groups[(record.size, digest)].append(
                DuplicateFile(record.path, record.size, digest, snapshot)
            )
        except (OSError, FileSafetyError) as exc:
            issues.append(ScanIssue(str(record.path), "hash", str(exc)))
        if progress is not None:
            progress(index, total, str(record.path))

    groups: list[DuplicateGroup] = []
    for (_, digest), matching in sorted(
        digest_groups.items(), key=lambda pair: (pair[0][0], pair[0][1])
    ):
        if len(matching) < 2:
            continue
        matching.sort(key=lambda item: (str(item.path).casefold(), str(item.path)))
        members = tuple(matching)
        default_keep = members[0].path
        groups.append(
            DuplicateGroup(
                sha256=digest,
                size=members[0].size,
                files=members,
                default_keep=default_keep,
                reclaimable_bytes=estimate_reclaimable_bytes(members, default_keep),
            )
        )
    return DuplicateAnalysis(
        groups=groups,
        issues=issues,
        files_hashed=files_hashed,
        bytes_hashed=bytes_hashed,
        duration_seconds=time.monotonic() - started,
    )


def select_retained_copies(
    groups: list[DuplicateGroup], requested_paths: list[str | Path] | None = None
) -> dict[str, Path]:
    """Return one retained path per group, validating explicit user selections."""
    requested = [Path(value).expanduser().resolve(strict=False) for value in requested_paths or []]
    group_paths = {
        path.resolve(strict=False): group
        for group in groups
        for path in (file.path for file in group.files)
    }
    selections: dict[str, Path] = {}
    selected_by_group: dict[str, Path] = {}
    for path in requested:
        group = group_paths.get(path)
        if group is None:
            raise ValueError(f"--keep path is not part of a detected duplicate group: {path}")
        if group.sha256 in selected_by_group:
            raise ValueError(
                f"Choose only one --keep path per duplicate group ({group.sha256[:12]})"
            )
        selected_by_group[group.sha256] = path
    for group in groups:
        keep = selected_by_group.get(group.sha256, group.default_keep.resolve(strict=False))
        selections[group.sha256] = keep
    return selections
