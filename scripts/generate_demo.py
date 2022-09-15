#!/usr/bin/env python3
"""Generate a disposable synthetic dataset for Smart File Organizer demos."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly from an uninstalled source checkout.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

from smart_file_organizer.demo import create_demo_data  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        default="smart-organizer-demo",
        help="New or empty directory to populate (default: ./smart-organizer-demo)",
    )
    args = parser.parse_args()
    try:
        directory = create_demo_data(args.directory)
    except (FileExistsError, NotADirectoryError, OSError) as exc:
        parser.error(str(exc))
    print(f"Created synthetic demo files: {directory}")
    print(f'Next: python -m smart_file_organizer scan "{directory}" --recursive')
    print(f'Preview moves safely: python -m smart_file_organizer preview "{directory}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
