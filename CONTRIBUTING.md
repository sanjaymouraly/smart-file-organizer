# Contributing

Thanks for helping improve Smart File Organizer. Safety, clear failure reporting, and honest verification matter more than adding features quickly.

## Development setup

Clone the repository, then use Python 3.11+ and install the project with development tools:

```bash
git clone https://github.com/sanjaymouraly/smart-file-organizer.git
cd smart-file-organizer
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

Run formatting, lint, and the branch-aware test suite before opening a pull request:

```bash
ruff format src tests scripts
ruff check src tests scripts
pytest --cov=smart_file_organizer --cov-branch --cov-report=term-missing
```

The checks above were run on Windows with Python 3.11. A local pass on one OS is not evidence that another OS was run successfully.

## Filesystem safety checklist

- Use only pytest's temporary directories or synthetic data from `smart-organizer demo` in automated tests. Never target personal folders.
- Keep scan, preview, and report paths non-mutating with respect to input files.
- Require explicit authorization for all file changes; never add an implicit apply or permanent-delete path.
- Never overwrite or automatically delete an existing destination or duplicate. Preserve source files until a verified destination exists.
- Record per-file operation state before and after changes; test recovery, undo, interruption, and tampered or stale paths.
- Prefer standard-library APIs, streaming I/O, `pathlib`, `argparse`, and bounded-memory hashing.
- Do not add network access, telemetry, credentials, paid services, or hidden user-data collection.
- Do not fabricate GUI screenshots. If a graphical environment is unavailable, document the limitation and add genuine screenshots only after real capture.

## Pull requests

- Explain the user-visible behavior and safety implications.
- Add focused tests for normal, error, and recovery paths.
- Update the README or user guide for changed commands/configuration.
- Describe user-visible changes in the pull request.
- Do not commit caches, coverage output, local configs, operation manifests, or personal data.
- Do not claim platform support or checks that have not been run. CI results should be reported separately from local verification.
