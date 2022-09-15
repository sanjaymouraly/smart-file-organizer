#!/usr/bin/env python3
"""Generate the architecture/workflow diagram at docs/images/workflow.png.

Renders a clean flowchart that mirrors the real pipeline using only the standard
library (zlib/struct), so no imaging dependency is required.

Usage: python scripts/generate_workflow.py [output.png]
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

W, H = 1200, 780
BG = (244, 247, 252)
BLUE = (21, 38, 74)
ACCENT = (37, 99, 235)
GREEN = (22, 128, 74)
AMBER = (180, 120, 20)
MUTED = (81, 98, 125)
CARD = (255, 255, 255)
BORDER = (203, 213, 230)

# Compact 5x7 bitmap font: each glyph is 7 rows of 5 bits.
GLYPHS = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "01010", "00100", "00100", "00100", "01010", "10001"],
    "Y": ["10001", "01010", "00100", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10011", "10101", "10101", "10101", "11001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "01100", "01100", "01000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "&": ["01100", "10010", "10100", "01000", "10101", "10010", "01101"],
    ">": ["01000", "00100", "00010", "00001", "00010", "00100", "01000"],
    "=": ["00000", "00000", "11111", "00000", "11111", "00000", "00000"],
    "#": ["01010", "01010", "11111", "01010", "11111", "01010", "01010"],
    "*": ["00000", "10101", "01110", "11111", "01110", "10101", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
}

SCALE = 2
GW, GH = 5, 7


def text_width(text: str) -> int:
    return len(text) * (GW + 1) * SCALE


def draw_text(px, text, x, y, color, scale=SCALE):
    cx = x
    for ch in text.upper():
        glyph = GLYPHS.get(ch, GLYPHS[" "])
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            _plot(px, cx + rx * scale + sx, y + ry * scale + sy, color)
        cx += (GW + 1) * scale
    return cx


def _plot(px, x, y, color):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 3
        px[i], px[i + 1], px[i + 2] = color


def fill_rect(px, x, y, w, h, color):
    for yy in range(max(0, y), min(H, y + h)):
        for xx in range(max(0, x), min(W, x + w)):
            _plot(px, xx, yy, color)


def rect_outline(px, x, y, w, h, color, thickness=2):
    fill_rect(px, x, y, w, thickness, color)
    fill_rect(px, x, y + h - thickness, w, thickness, color)
    fill_rect(px, x, y, thickness, h, color)
    fill_rect(px, x + w - thickness, y, thickness, h, color)


def arrow_down(px, x, y1, y2, color):
    fill_rect(px, x - 2, y1, 5, y2 - y1, color)
    for i in range(8):
        fill_rect(px, x - 2 - i, y2 - 8 + i, 5 + 2 * i, 3, color)


def arrow_right(px, x1, x2, y, color):
    fill_rect(px, x1, y - 2, x2 - x1, 5, color)
    for i in range(8):
        fill_rect(px, x2 - 8 + i, y - 2 - i, 3, 5 + 2 * i, color)


def card(px, x, y, w, h, title, subtitle, color, fill=CARD):
    fill_rect(px, x, y, w, h, fill)
    rect_outline(px, x, y, w, h, color, 3)
    draw_text(px, title, x + 14, y + 12, color)
    if subtitle:
        draw_text(px, subtitle, x + 14, y + 12 + (GH + 2) * SCALE + 6, MUTED, scale=1)


def center_text(px, text, x, y, w, color, scale=SCALE):
    draw_text(px, text, x + (w - text_width(text)) // 2, y, color, scale=scale)


def write_png(path, px):
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        row = px[y * W * 3 : (y + 1) * W * 3]
        raw.extend(row)

    def chunk(kind, payload):
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    Path(path).write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    default_out = Path(__file__).resolve().parents[1] / "docs" / "images" / "workflow.png"
    out = Path(args[0]).expanduser() if args else default_out

    px = bytearray()
    for _ in range(W * H):
        px.extend(BG)

    # Title
    center_text(px, "SMART FILE ORGANIZER", 0, 22, W, BLUE, scale=3)
    center_text(
        px,
        "SAFE PIPELINE: SCAN > CLASSIFY > PLAN > APPLY > HISTORY > UNDO",
        0,
        60,
        W,
        MUTED,
        scale=1,
    )

    # Row 1: Input
    card(px, 30, 90, 180, 76, "USER INPUT", "FOLDER / CLI OR GUI", ACCENT)
    card(px, 250, 90, 180, 76, "SCANNER", "WALKS TREE, NO WRITES", ACCENT)
    card(px, 470, 90, 180, 76, "CLASSIFIER", "EXT + SIGNATURE", ACCENT)
    card(px, 690, 90, 180, 76, "ORGANIZER", "BUILD SAFE PLAN", ACCENT)
    card(px, 910, 90, 260, 76, "DUPLICATES", "SIZE THEN SHA-256 GROUP", ACCENT)

    for x0, x1 in ((210, 250), (430, 470), (650, 690), (870, 910)):
        arrow_right(px, x0, x1, 128, MUTED)

    # Row 2: safety + apply
    y2 = 210
    card(px, 30, y2, 250, 86, "SAFETY LAYER", "SNAPSHOT + SHA-256", GREEN)
    card(px, 320, y2, 250, 86, "PREVIEW = DRY RUN", "DEFAULT, MOVES NOTHING", GREEN)
    card(px, 610, y2, 250, 86, "APPLY ONLY WITH", "EXPLICIT --APPLY / GUI OK", GREEN)
    card(px, 900, y2, 270, 86, "MANIFEST", "ATOMIC JSON, PER FILE STATE", GREEN)

    arrow_down(px, 120, 166, y2, MUTED)  # user -> safety
    arrow_down(px, 340, 166, y2, MUTED)  # classifier -> safety
    arrow_down(px, 780, 166, y2, MUTED)  # organizer -> apply
    arrow_down(px, 1040, 166, y2, MUTED)  # duplicates -> manifest

    for x0, x1 in ((280, 320), (570, 610), (860, 900)):
        arrow_right(px, x0, x1, y2 + 43, MUTED)

    # Row 3: outcomes
    y3 = 340
    card(px, 30, y3, 250, 86, "MOVES", "COPY > VERIFY > PUBLISH", ACCENT)
    card(px, 320, y3, 250, 86, "NO OVERWRITE", "NEVER DELETES FILES", AMBER)
    card(px, 610, y3, 250, 86, "QUARANTINE", "OPTIONAL, REVERSIBLE", AMBER)
    card(px, 900, y3, 270, 86, "REPORTS", "JSON / CSV / HTML", ACCENT)

    arrow_down(px, 155, y2 + 86, y3, MUTED)
    arrow_down(px, 735, y2 + 86, y3, MUTED)
    arrow_down(px, 1035, y2 + 86, y3, MUTED)

    # Row 4: history / undo
    y4 = 470
    card(px, 30, y4, 480, 86, "OPERATION HISTORY", "PERSISTED, RESTART SAFE", BLUE)
    card(px, 620, y4, 550, 86, "UNDO / RECOVER", "SAFE, VERIFIED, NO CLOBBER", BLUE)

    arrow_down(px, 155, y3 + 86, y4, MUTED)
    arrow_right(px, 510, 620, y4 + 43, MUTED)

    # Legend / guarantee bar
    fill_rect(px, 30, 600, W - 60, 120, CARD)
    rect_outline(px, 30, 600, W - 60, 120, BORDER, 3)
    center_text(px, "GUARANTEES", 30, 612, W - 60, BLUE, scale=2)
    lines = [
        "PREVIEW IS THE DEFAULT - NOTHING MOVES WITHOUT EXPLICIT CONSENT",
        "SOURCE REMOVED ONLY AFTER VERIFIED DESTINATION EXISTS",
        "NO OVERWRITE - NAME CONFLICTS GET A NUMBERED SUFFIX",
        "DUPLICATES ARE REPORTED, NEVER AUTO-DELETED - QUARANTINE IS OPTIONAL",
    ]
    yy = 648
    for line in lines:
        center_text(px, line, 30, yy, W - 60, MUTED, scale=1)
        yy += 16

    out.parent.mkdir(parents=True, exist_ok=True)
    write_png(out, px)
    print(f"saved {out} ({W}x{H})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
