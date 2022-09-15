# Safety model and operational limits

Smart File Organizer is deliberately preview-first, but no program that changes a filesystem can guarantee recovery from every hardware, operating-system, or user failure. Make a separate backup of important data before applying organization or quarantine operations.

## Default behavior

- `scan`, `preview`, duplicate analysis, and report generation do not move or delete source files.
- Organization moves require `organize --apply`; duplicate quarantine requires both `duplicates --quarantine --apply`. The GUI asks for confirmation before organization apply/undo.
- There is no permanent-delete operation. Duplicate quarantine moves extra copies to application state and can be undone.
- Destination publishing is no-overwrite. Existing targets trigger a numbered plan name or a reported failure; the mover does not intentionally replace them.
- Symlinks and special files are skipped by scanning. Source and destination paths are checked against their configured roots before moving.

## How a move is protected

For each planned file, the application rechecks its captured metadata and SHA-256; streams it to an exclusive temporary path; verifies the copied digest; publishes the new destination without replacing an existing entry; verifies the published content; rechecks the source; and only then removes the source path. The source is not removed until a verified destination exists. On filesystems without usable hard links, publication falls back to an exclusive copy. An interrupted copy may leave a temporary or partial destination, or both source and destination copies; the manifest records per-file progress so recovery can report rather than guess.

This is a verified per-file operation, not an atomic batch transaction. A power loss, filesystem error, abrupt process termination, or concurrent external program can still create an ambiguous state. Recovery intentionally does not delete an unexpected file or resolve two-copy states automatically.

## Undo and recovery

- Undo checks that the destination still matches the manifest and refuses to overwrite an occupied original path.
- Recovery only marks a move complete when the expected destination digest verifies and the source is absent. If both paths exist or contents disagree, it leaves files untouched and asks for inspection.
- Manifests are required for reliable undo/recovery. Do not delete or edit them while operations are in progress. Keep the state directory backed up if you depend on its history.
- A manifest contains absolute paths, file sizes, hashes, timestamps, and status. Protect it like other local metadata; do not publish it if paths are sensitive.

## Metadata and filesystem caveats

The implementation streams file contents and preserves basic mode and timestamps where supported. Copy-based operations are not guaranteed to preserve every platform-specific property: ownership, ACLs, extended attributes, alternate data streams, resource forks, hard-link relationships, sparse-file layout, and special filesystem flags may differ. Cross-volume moves may require temporary disk space approximately equal to the file size. Very large files can take time to fingerprint and copy.

Metadata comparisons differ across filesystems. If an operation fails a safety check, do not weaken it by editing the manifest or deleting files; inspect the paths and make a separate backup first.

On Windows the change-time (`ctime`) is deliberately excluded from stability checks. Creating the hard link that publishes each staged file updates the change-time asynchronously and can report an earlier value than a prior read, so comparing it would reject correct operations. Size and modification time remain the pre-filter markers there, and content is verified with SHA-256 before a source is ever removed. On POSIX the change-time is synchronous and monotonic and is still compared.

## Detection and privacy limits

- SHA-256 duplicate analysis compares file size and digest. It is designed for ordinary duplicate detection, not adversarial proof against hypothetical cryptographic collisions.
- Signature checks recognize a limited set of common file headers. They do not parse files, validate complete formats, scan for malware, or guarantee that a file is safe to open.
- Reports and manifests can contain local paths and names. The program has no telemetry or network dependency; users control whether saved reports are shared.

## Test safety

Automated tests create temporary synthetic files under pytest's temporary directory and monkeypatch failure conditions where practical. They do not intentionally scan, move, overwrite, quarantine, or delete personal files. CI tests code behavior on configured operating-system runners; a passing test suite cannot cover every filesystem, permission model, antivirus product, network share, or power-loss condition.
