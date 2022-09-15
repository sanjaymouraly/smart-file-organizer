# Smart File Organizer & Duplicate Detector

A local-first Python tool that organizes messy folders and finds exact duplicate files. It includes a command-line interface and an optional Tkinter desktop app. Runtime code uses only the Python standard library.

The default mode is safe: scanning, previews, reports, and duplicate checks never move files. Applying changes requires `--apply` (or confirmation in the GUI). Existing files are never overwritten, duplicates are never permanently deleted, and every applied move is recorded in a manifest so it can be undone or recovered.

## Features

- Recursive scanning with hidden-file and exclusion controls.
- Extension and lightweight signature-based classification.
- Dry-run organization preview with collision-safe names.
- Optional year/month and extension grouping.
- Verified copy → verify → publish moves with no overwrites.
- SHA-256 duplicate detection with optional reversible quarantine.
- Persistent operation history with undo and recovery.
- JSON, CSV, and HTML reports.
- Synthetic demo data generator.
- Optional Tkinter GUI that uses the same engine as the CLI.

## Requirements

- Python 3.11 or newer.
- Windows 10/11 or Linux.
- Tkinter only for the GUI. It ships with the Windows python.org installer; on Debian/Ubuntu install `python3-tk`.

## Clone and install

```bash
git clone https://github.com/sanjaymouraly/smart-file-organizer.git
cd smart-file-organizer
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Linux/macOS:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

If activation is blocked, call the interpreter directly:
`python -m smart_file_organizer ...`.

## Quick start

Practice on generated data first:

```text
smart-organizer demo ./organizer-demo
smart-organizer scan ./organizer-demo --recursive
smart-organizer preview ./organizer-demo
```

Apply the plan only after reviewing it:

```text
smart-organizer organize ./organizer-demo --apply
smart-organizer history
smart-organizer undo OPERATION_ID
smart-organizer recover OPERATION_ID
```

## Commands

| Command | Effect |
| --- | --- |
| `smart-organizer scan PATH` | Summarize files without changing them. |
| `smart-organizer preview PATH` | Show a dry-run organization plan. |
| `smart-organizer organize PATH` | Preview only; add `--apply` to execute. |
| `smart-organizer duplicates PATH` | Find exact duplicate contents. |
| `smart-organizer duplicates PATH --quarantine` | Preview a reversible quarantine. |
| `smart-organizer duplicates PATH --quarantine --apply` | Execute the quarantine. |
| `smart-organizer history` | List saved operations. |
| `smart-organizer undo ID` | Restore eligible moved files. |
| `smart-organizer recover ID` | Reconcile an interrupted operation. |
| `smart-organizer report ID --format html --output report.html` | Export an operation report. |
| `smart-organizer gui` | Start the optional desktop app. |
| `smart-organizer config show` | Show effective settings. |

Useful options: `--no-recursive`, `--include-hidden`, `--exclude PATTERN`, `--destination PATH`, `--by-month`, `--by-extension`, `--report PATH`, and `--format json|csv|html`.

## Duplicate handling

Files are grouped by size first, then hashed with streaming SHA-256. Duplicate copies are reported, never deleted. The explicit cleanup path is quarantine: extra copies move into application storage and can be undone like any other operation.

## Configuration

Starter settings live in `examples/config.json`. Create a per-user config with:

```text
smart-organizer config init
smart-organizer config show
```

Pass `--config PATH` to use a specific file. Configuration controls classification rules, excluded folders, destination layout, filename templates, and state storage.

## Safety

- Preview is the default; nothing moves without `--apply` or GUI confirmation.
- A source file is removed only after a verified destination copy exists.
- Existing files are never overwritten; collisions get numbered suffixes.
- Duplicates are never auto-deleted; quarantine is optional and reversible.
- Every apply is recorded in a per-file manifest for undo and recovery.

Limitations: copy-based moves do not preserve ACLs, ownership, extended attributes, alternate data streams, or hard-link relationships. A power loss can leave a temporary file or two copies; `recover` reports the state instead of guessing.

## Tests

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

The suite uses generated temporary files and never touches personal data.

## License

MIT © 2022 [Sanjay Mouraly](https://github.com/sanjaymouraly)
