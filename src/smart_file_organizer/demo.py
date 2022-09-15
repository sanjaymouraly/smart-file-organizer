"""Generate small, synthetic sample files for safe demonstrations and testing."""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def _minimal_pdf(text: str) -> bytes:
    """Create a tiny one-page PDF with a correct xref table."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 18 Tf 50 740 Td ({safe_text}) Tj ET".encode("ascii", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n% Smart File Organizer synthetic demo\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(output)


def _png_pixel(red: int, green: int, blue: int) -> bytes:
    """Create a valid 1x1 RGB PNG using only the Python standard library."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    raw = b"\x00" + bytes((red, green, blue))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _minimal_xlsx() -> bytes:
    """Create a small, readable OOXML workbook without optional dependencies."""
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        workbook.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Budget" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        workbook.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Item</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>Amount</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>Travel</t></is></c>'
            '<c r="B2"><v>120</v></c></row></sheetData></worksheet>',
        )
    return buffer.getvalue()


def create_demo_data(directory: str | Path) -> Path:
    """Create an isolated mixed dataset; refuses to overwrite non-empty directories."""
    target = Path(directory).expanduser().resolve(strict=False)
    if target.exists():
        if not target.is_dir():
            raise NotADirectoryError(f"Demo target exists and is not a directory: {target}")
        if any(target.iterdir()):
            raise FileExistsError(f"Refusing to populate a non-empty demo directory: {target}")
    else:
        target.mkdir(parents=True)

    report = _minimal_pdf("Synthetic demo report - no personal data")
    notes = b"These synthetic notes demonstrate exact duplicate detection.\n"
    files: dict[str, bytes] = {
        "report.pdf": report,
        "nested/report.pdf": report,
        "budget.xlsx": _minimal_xlsx(),
        "budget.csv": b"Item,Amount\nTravel,120\nSupplies,35\n",
        "holiday.png": _png_pixel(50, 145, 230),
        "notes.txt": notes,
        "notes-copy.txt": notes,
        "script.PY": b"print('Synthetic demo file')\n",
        "archive.zip": _zip_sample(),
        "r\u00e9sum\u00e9 final \U0001f4c4.txt": b"Unicode filename demo.\n",
        "no-extension": b"Unknown extension example.\n",
        ".hidden-demo.txt": b"Hidden-file filtering example.\n",
    }
    for relative, content in files.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(content)
    return target


def _zip_sample() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", "Synthetic archive sample.\n")
    return buffer.getvalue()
