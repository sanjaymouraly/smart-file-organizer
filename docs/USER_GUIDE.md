# User guide

This guide assumes the package is installed in an active Python 3.11+ environment. On Windows, quote paths containing spaces and use the PowerShell command names shown below. On Linux, quote paths containing spaces in Bash. If the `smart-organizer` console command is not on `PATH`, replace it with `python -m smart_file_organizer`.

## 1. Make a safe practice folder

The demo generator creates synthetic PDF, XLSX, CSV, PNG, ZIP, code, text, hidden, Unicode-named, and duplicate files. It refuses to populate a non-empty directory and never uses personal files.

```text
smart-organizer demo "./organizer-demo"
smart-organizer scan "./organizer-demo" --recursive
smart-organizer preview "./organizer-demo"
```

You can run the same generator directly from a source checkout:

```text
python scripts/generate_demo.py "./organizer-demo"
```

A scan and preview do not move or delete files. The demo includes a hidden file, so it will not appear unless you pass `--include-hidden` or enable `include_hidden` in the configuration.

## 2. Scan a directory

```text
smart-organizer scan "C:\Users\You\Downloads" --recursive
smart-organizer scan "/home/you/Downloads" --no-recursive
```

Scanning is recursive by default. The scanner:

- visits regular files only and skips symbolic links and special files;
- skips hidden files/directories by default, including Windows hidden-attribute files;
- skips common dependency, VCS, and application-state directories by default;
- excludes the selected managed destination, state directory, config file, and requested report path where applicable;
- records recoverable access/signature-read warnings and continues with other paths.

Use `--include-hidden` to include hidden entries. Repeat `--exclude` to add a basename or glob pattern, for example:

```text
smart-organizer scan "./project" --exclude "cache" --exclude "*.tmp"
```

The default output summarizes categories and total bytes. To write a report, select a new filename:

```text
smart-organizer scan "./project" --report "./scan-report.html" --format html
```

Supported report formats are `json`, `csv`, and `html`. The output path is created exclusively; if it already exists, choose another filename rather than expecting it to be replaced. CSV values that begin with spreadsheet formula characters are prefixed to reduce formula-injection risk.

## 3. Preview and apply organization

Preview first:

```text
smart-organizer preview "./organizer-demo"
```

The proposed destination defaults to `PATH/Organized`, and files are grouped by category. Use options to select a different root or layout:

```text
smart-organizer preview "./organizer-demo" --destination "./sorted" --by-month
smart-organizer preview "./organizer-demo" --by-extension
smart-organizer preview "./organizer-demo" --report "./plan.json"
```

`--by-month` appends year and month folders unless the configured layout already contains both `{year}` and `{month}`. `--by-extension` appends an extension folder unless `{extension}` is already in the layout. Unknown extensions are categorized as `Other`. Extension/signature mismatches that can be recognized are also classified as `Other` with a warning; signature checks are intentionally small and do not certify that a file is safe to open.

Only `organize --apply` performs the planned move:

```text
smart-organizer organize "./organizer-demo" --apply
```

Read the preview and the exact source-to-destination list before confirming the operation. If a target name exists (including a case-only collision on common case-insensitive filesystems), the planner proposes a numbered name instead of overwriting. The apply step rechecks file identity and content against the preview; changed, missing, or unsafe source files are not treated as successful moves.

Each apply stores a persistent manifest with the operation ID, source and destination paths, per-file state, and hashes. Keep the application state directory intact if you may need to undo or recover an operation.

## 4. Find duplicates and optionally quarantine copies

Duplicate analysis first filters by file size, then hashes candidates with SHA-256. Results are grouped by size and digest; one path is selected as the default retained copy using a deterministic path sort. Analysis itself never changes files:

```text
smart-organizer duplicates "./organizer-demo"
```

To preview moving the extra copies into a reversible quarantine inside the application state directory:

```text
smart-organizer duplicates "./organizer-demo" --quarantine
```

The command still does not move files until the explicit apply flag is present:

```text
smart-organizer duplicates "./organizer-demo" --quarantine --apply
```

You may specify one path per duplicate group to keep, repeating the option if needed:

```text
smart-organizer duplicates "./organizer-demo" --quarantine --keep "./organizer-demo/nested/report.pdf"
```

`--keep` is valid only with `--quarantine`. Quarantine moves are recorded like other operations and can be reversed with the same `undo OPERATION_ID` command. There is no permanent-delete command.

## 5. Review history, undo, and recover

List saved operation IDs:

```text
smart-organizer history
```

Undo a completed organize or quarantine operation by ID:

```text
smart-organizer undo 0123456789abcdef0123456789abcdef
```

Undo restores only entries that remain eligible. It refuses to overwrite an occupied original path and refuses a destination whose contents or important recorded metadata changed. Review skipped/failed entries, preserve both copies, and decide manually. Do not remove a file merely to force a clean undo.

After an interruption or a partial apply, inspect the manifest and reconcile safely:

```text
smart-organizer recover 0123456789abcdef0123456789abcdef
```

Recovery does not delete, overwrite, or guess about ambiguous files. It advances a move only when the expected destination content verifies and the source path is absent; pending, mismatched, and two-copy states are explicitly reported. If the machine lost power while a copy was in progress, inspect the reported source, destination, and any operation-specific temporary file before deciding what to do.

Export a report for one saved operation:

```text
smart-organizer report 0123456789abcdef0123456789abcdef --format html --output "./operation-report.html"
```

## 6. Optional desktop GUI

Start the GUI with:

```text
smart-organizer gui
```

Choose a folder and select **Scan & preview**. The file table shows the source paths and proposed destinations. The duplicate pane is informational. **Apply moves…** opens a separate confirmation before organization. The recent-operation selector can be used to review and undo eligible operations. GUI scanning and file work run on worker threads; progress events are displayed in the window.

The GUI requires a working Tkinter installation and a graphical session. Headless shells, containers without a display, and many remote sessions cannot open the window. See [screenshots and GUI capture status](SCREENSHOTS.md).

## Configuration

### Create or inspect preferences

Create a starter config at the platform's per-user location:

```text
smart-organizer config init
smart-organizer config show
```

Initialization will not overwrite an existing file. Use `smart-organizer config init --force` only when you explicitly want to replace the selected config with defaults. You can also supply a path, or use the tracked starter file:

```text
smart-organizer config init "./my-config.json"
smart-organizer scan "./organizer-demo" --config "./examples/config.json"
```

The config file is JSON. `examples/config.json` is a complete valid template. If you select a missing config explicitly, the command reports an error rather than silently substituting defaults. Files written by Windows editors (Notepad, PowerShell's `Set-Content -Encoding UTF8`) are accepted even when they begin with a UTF-8 byte-order mark.

### Options

| Key | Type / default | Description |
| --- | --- | --- |
| `version` | integer, `1` | Config schema version. |
| `storage_dir` | path or `null` | Location for manifests and duplicate quarantine. A relative path is relative to the config file (or current working directory if no config-file path is attached). `null` uses the OS default. |
| `include_hidden` | boolean, `false` | Include hidden files and directories by default. |
| `excluded_dirs` | list of names | Exact directory basenames always skipped. Includes common `.git`, virtualenv, cache, and application state folders by default. |
| `excluded_patterns` | list of globs, empty | Patterns matched against relative paths and basenames, for files and directories. CLI `--exclude` adds patterns for one command. |
| `destination_name` | single safe name, `Organized` | Default output folder created inside the selected scan root. |
| `destination_layout` | relative template, `{category}` | Folder template. Supported fields: `{category}`, `{year}`, `{month}`, `{extension}`. Absolute paths, empty components, and traversal are rejected. |
| `filename_template` | template, `{name}` | Filename template. Supported fields: `{name}`, `{stem}`, `{suffix}`, `{extension}`. It must produce one filename; unsafe characters are sanitized. |
| `signature_validation` | boolean, `true` | Read a small file prefix to cross-check known signatures. This is not a malware scan. |
| `hash_chunk_size` | integer, 1 MiB | Streaming hash chunk size, from 4 KiB to 16 MiB. |
| `extensions` | category-to-list object | Extension-to-category map; each extension starts with a dot and may appear in only one category. |

The default user config location is `%APPDATA%\SmartFileOrganizer\config.json` on Windows and `$XDG_CONFIG_HOME/smart-file-organizer/config.json` on Linux (falling back to `~/.config`). The default operation-state directory is `%LOCALAPPDATA%\SmartFileOrganizer` on Windows and `$XDG_STATE_HOME/smart-file-organizer` on Linux (falling back to `~/.local/state`). On macOS or other platforms, consult the effective paths on that platform; this project currently documents and targets Windows and Linux.

Manifests and reports contain local paths and may reveal file names. Protect and share them accordingly. The application does not upload data or use telemetry.

## Troubleshooting

- **`smart-organizer` is not recognized:** activate the virtual environment, or use `python -m smart_file_organizer` from that environment.
- **Permission denied / scan warnings:** the scanner continues where possible. Run with suitable read permission, or exclude the inaccessible path. Do not use administrator/root privileges unless necessary and understood.
- **GUI reports no display / Tk error:** install/enable Tkinter and use a local graphical session; continue with the CLI in a headless environment.
- **A config or report already exists:** choose a new output path. Use `config init --force` only when intentionally replacing a config.
- **An apply is partial or interrupted:** retain all source, destination, state, and temporary files; use `history` and `recover OPERATION_ID` to inspect. Do not manually delete either copy until its contents are understood.
- **Undo skips a file:** inspect the original and organized paths. The tool intentionally leaves occupied or changed files untouched.
- **A filename looks garbled in the console:** legacy Windows code pages cannot display every Unicode character. The tool prints a readable `\UXXXXXXXX` escape instead of crashing. The real filename on disk is unaffected; use `--report ... --format json` for exact names.
