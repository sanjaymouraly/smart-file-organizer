"""Extension-based classification with small, non-executing signature checks."""

from __future__ import annotations

from pathlib import Path

from .config import DEFAULT_EXTENSION_CATEGORIES, Config
from .models import Classification

_SIGNATURES: tuple[tuple[str, bytes, str], ...] = (
    ("PDF", b"%PDF-", "Documents"),
    ("PNG", b"\x89PNG\r\n\x1a\n", "Images"),
    ("JPEG", b"\xff\xd8\xff", "Images"),
    ("GIF", b"GIF87a", "Images"),
    ("GIF", b"GIF89a", "Images"),
    ("WEBP", b"WEBP", "Images"),
    ("BMP", b"BM", "Images"),
    ("TIFF_LE", b"II*\x00", "Images"),
    ("TIFF_BE", b"MM\x00*", "Images"),
    ("ZIP", b"PK\x03\x04", "Archives"),
    ("ZIP", b"PK\x05\x06", "Archives"),
    ("ZIP", b"PK\x07\x08", "Archives"),
    ("RAR", b"Rar!\x1a\x07", "Archives"),
    ("7Z", b"7z\xbc\xaf\x27\x1c", "Archives"),
    ("GZIP", b"\x1f\x8b", "Archives"),
    ("OLE", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "Office"),
    ("FLAC", b"fLaC", "Audio"),
    ("OGG", b"OggS", "Audio"),
    ("ID3", b"ID3", "Audio"),
    ("WAVE", b"WAVE", "Audio"),
    ("AVI", b"AVI ", "Videos"),
    ("MP4", b"ftyp", "Videos"),
    ("TAR", b"ustar", "Archives"),
)

_EXPECTED_SIGNATURES: dict[str, set[str]] = {
    ".pdf": {"PDF"},
    ".png": {"PNG"},
    ".jpg": {"JPEG"},
    ".jpeg": {"JPEG"},
    ".gif": {"GIF"},
    ".webp": {"WEBP"},
    ".bmp": {"BMP"},
    ".tif": {"TIFF_LE", "TIFF_BE"},
    ".tiff": {"TIFF_LE", "TIFF_BE"},
    ".zip": {"ZIP"},
    ".rar": {"RAR"},
    ".7z": {"7Z"},
    ".gz": {"GZIP"},
    ".tgz": {"GZIP"},
    ".doc": {"OLE"},
    ".xls": {"OLE"},
    ".ppt": {"OLE"},
    ".docx": {"ZIP"},
    ".xlsx": {"ZIP"},
    ".pptx": {"ZIP"},
    ".odt": {"ZIP"},
    ".ods": {"ZIP"},
    ".odp": {"ZIP"},
    ".flac": {"FLAC"},
    ".ogg": {"OGG"},
    ".mp3": {"ID3", "MPEG_AUDIO"},
    ".m4a": {"MP4"},
    ".wav": {"WAVE"},
    ".avi": {"AVI"},
    ".mp4": {"MP4"},
    ".m4v": {"MP4"},
    ".mov": {"MP4"},
}

_OFFICE_ZIP_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
_OFFICE_OLE_EXTENSIONS = {".doc", ".xls", ".ppt"}


def extension_categories(config: Config | None = None) -> dict[str, str]:
    """Build a normalized extension-to-category lookup."""
    categories = config.extension_categories if config else DEFAULT_EXTENSION_CATEGORIES
    return {
        extension.lower(): category
        for category, extensions in categories.items()
        for extension in extensions
    }


def identify_signature(header: bytes) -> str | None:
    """Identify a small set of common file signatures; never parses file contents."""
    for signature_name, marker, _ in _SIGNATURES:
        if signature_name == "WEBP":
            if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
                return signature_name
            continue
        if signature_name == "WAVE":
            if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == marker:
                return signature_name
            continue
        if signature_name == "AVI":
            if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == marker:
                return signature_name
            continue
        if signature_name == "MP4":
            if len(header) >= 8 and header[4:8] == marker:
                return signature_name
            continue
        if signature_name == "TAR":
            if len(header) >= 262 and header[257:262] == marker:
                return signature_name
            continue
        if header.startswith(marker):
            return signature_name
    # An MPEG audio frame commonly starts with 0xFF and eleven set sync bits.
    if len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0:
        return "MPEG_AUDIO"
    return None


def classify_file(
    path: Path,
    *,
    config: Config | None = None,
    header: bytes | None = None,
    signature_validation: bool | None = None,
) -> Classification:
    """Classify by extension and validate well-known binary signatures when available."""
    categories = extension_categories(config)
    extension = path.suffix.lower()
    category = categories.get(extension, "Other")
    validate = (
        config.signature_validation
        if signature_validation is None and config
        else signature_validation
    )
    if validate is None:
        validate = True
    if not validate or header is None:
        return Classification(category=category, extension=extension)

    signature = identify_signature(header)
    expected = _EXPECTED_SIGNATURES.get(extension)
    if expected is not None and signature not in expected:
        return Classification(
            category="Other",
            extension=extension,
            signature=signature,
            warning=(
                f"Extension {extension} does not match its expected file signature; "
                "classified as Other for safety."
            ),
        )

    if signature is None:
        return Classification(category=category, extension=extension)

    if signature == "Office":  # Reserved for future signature expansion.
        return Classification(category=category, extension=extension, signature=signature)

    signature_category = next(
        (category_name for name, _, category_name in _SIGNATURES if name == signature), None
    )
    if signature == "MPEG_AUDIO":
        signature_category = "Audio"
    if signature == "OLE":
        if extension in _OFFICE_OLE_EXTENSIONS:
            return Classification(category=category, extension=extension, signature=signature)
        return Classification(
            category="Other",
            extension=extension,
            signature=signature,
            warning="Office compound-file signature does not match the filename extension.",
        )
    if signature == "ZIP" and extension in _OFFICE_ZIP_EXTENSIONS:
        return Classification(category=category, extension=extension, signature=signature)
    if signature == "MP4" and extension == ".m4a":
        return Classification(category=category, extension=extension, signature=signature)

    if category == "Other":
        return Classification(
            category=signature_category or "Other",
            extension=extension,
            signature=signature,
            warning=(
                f"Recognized {signature} signature despite an unrecognized extension."
                if signature_category
                else None
            ),
        )
    if signature_category and category != signature_category:
        return Classification(
            category="Other",
            extension=extension,
            signature=signature,
            warning=(
                f"Extension {extension or '(none)'} suggests {category}, but the file signature "
                f"suggests {signature_category}; classified as Other."
            ),
        )
    return Classification(category=category, extension=extension, signature=signature)
