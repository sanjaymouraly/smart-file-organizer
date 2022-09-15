#!/usr/bin/env python3
"""Launch the GUI, drive a scan, and capture the application window to PNG.

This is a Windows-only documentation helper (it uses GDI via ctypes) that runs in
the foreground on a machine with a display. The scan runs on the GUI's worker
thread; once the preview is rendered the window is captured and saved, then the
process exits.

Usage: python scripts/capture_gui.py <demo_folder> <output_png>
"""

from __future__ import annotations

import ctypes
import struct
import sys
import time
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

import tkinter as tk  # noqa: E402

from smart_file_organizer.config import Config  # noqa: E402
from smart_file_organizer.gui import OrganizerApp  # noqa: E402

GDI32 = ctypes.windll.gdi32
USER32 = ctypes.windll.user32
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def capture_window(hwnd: int, left: int, top: int, width: int, height: int, output: Path) -> None:
    """Copy the window's on-screen pixels to a PNG via GDI BitBlt + GetDIBits."""
    screen_dc = USER32.GetDC(0)
    memory_dc = GDI32.CreateCompatibleDC(screen_dc)
    bitmap = GDI32.CreateCompatibleBitmap(screen_dc, width, height)
    GDI32.SelectObject(memory_dc, bitmap)
    GDI32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, left, top, SRCCOPY)

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = height  # bottom-up DIB; row 0 is the bottom of the image
    header.biPlanes = 1
    header.biBitCount = 24
    header.biCompression = 0

    row_size = (width * 3 + 3) & ~3
    buffer = ctypes.create_string_buffer(row_size * height)
    GDI32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    pixel_data = buffer.raw
    # BMP rows are BGR and bottom-up in the file; convert to a minimal PNG via zlib.
    png = _bmp_to_png(width, height, row_size, pixel_data)
    output.write_bytes(png)

    GDI32.DeleteObject(bitmap)
    GDI32.DeleteDC(memory_dc)
    USER32.ReleaseDC(0, screen_dc)


def _bmp_to_png(width: int, height: int, row_size: int, data: bytes) -> bytes:
    """Encode a bottom-up BGR buffer as a PNG using only zlib/struct."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    # Expand BGR -> RGB and flip to top-down, adding a filter byte per row.
    bpp = 3
    rgb_row = width * bpp
    raw = bytearray()
    for y in range(height - 1, -1, -1):
        raw.append(0)  # filter type 0 (None)
        row_start = y * row_size
        row = data[row_start : row_start + width * bpp]
        # BGR to RGB
        converted = bytearray(rgb_row)
        converted[0::3] = row[2::3]
        converted[1::3] = row[1::3]
        converted[2::3] = row[0::3]
        raw.extend(converted)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolor
    compressed = zlib.compress(bytes(raw), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: gui_capture.py <demo_folder> <output_png>", file=sys.stderr)
        return 2
    demo = str(Path(sys.argv[1]).expanduser().resolve(strict=False))
    output = Path(sys.argv[2]).expanduser().resolve(strict=False)

    config = Config()
    config.storage_dir = str(Path(demo).resolve().parent / "sfo-gui-state")

    root = tk.Tk()
    app = OrganizerApp(root, config)
    app.folder_var.set(demo)
    app.destination_var.set(str(Path(demo) / config.destination_name))
    root.update_idletasks()
    root.geometry("1180x820+30+10")
    root.deiconify()
    root.lift()
    root.focus_force()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    root.update()

    app._start_scan()

    deadline = time.time() + 120
    while time.time() < deadline:
        root.update()
        status = app.status_var.get()
        if "Scan complete" in status or "failed" in status:
            break
        time.sleep(0.1)

    # Let the final render settle, then capture the window from the screen.
    root.update()
    time.sleep(0.4)

    rect = RECT()
    USER32.GetWindowRect(root.winfo_id(), ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    print(f"window rect: {rect.left},{rect.top} {width}x{height}", flush=True)
    if width < 200 or height < 200:
        print("window not mapped; cannot capture", file=sys.stderr)
        return 1

    # Bring to front and re-capture the on-screen pixels.
    USER32.SetForegroundWindow(root.winfo_id())
    root.update()
    time.sleep(0.3)
    USER32.GetWindowRect(root.winfo_id(), ctypes.byref(rect))
    capture_window(root.winfo_id(), rect.left, rect.top, width, height, output)
    print(f"saved {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
