"""Shared fixtures for realistic, disposable filesystem tests."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from smart_file_organizer.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(storage_dir=str(tmp_path / "state"), config_file=tmp_path / "config.json")


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    root = tmp_path / "messy files"
    (root / "nested").mkdir(parents=True)
    (root / "report.pdf").write_bytes(b"%PDF-1.4\nfixture report\n")
    (root / "nested" / "report.pdf").write_bytes(b"%PDF-1.4\nfixture report\n")
    (root / "budget.XLSX").write_bytes(b"PK\x03\x04synthetic workbook")
    (root / "notes.txt").write_text("same content\n", encoding="utf-8")
    (root / "notes copy.txt").write_text("same content\n", encoding="utf-8")
    (root / "bad.jpg").write_text("not a JPEG", encoding="utf-8")
    (root / "résumé final (draft).txt").write_text("Unicode filename\n", encoding="utf-8")
    (root / "no-extension").write_bytes(b"unknown\n")
    (root / ".hidden.txt").write_text("hidden\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    return root


def make_png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def try_symlink(target: Path, link: Path, *, is_directory: bool = False) -> bool:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (OSError, NotImplementedError):
        return False
    return True
