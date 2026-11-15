# Component specification

The package lives under `src/backup_verifier/`. Each command enters through the same CLI, uses a shared local filesystem and manifest model, and renders its result through `reporting.py`.

| Component | Entry points and models | Responsibility | Internal dependencies |
| --- | --- | --- | --- |
| Package entry points | `backup-verifier`, `python -m backup_verifier` | Invoke `cli.main()` with the process arguments | `cli` |
| `cli.py` | `main(argv)` | Parse arguments, resolve the scan output, dispatch commands, and select the process exit code | `scanner`, `hashing`, `manifest`, `verification`, `comparison`, `reporting` |
| `scanner.py` | `scan_root`, `walk_tree`, `scan_tree`, `ScanRules`, `Inventory`, `FileEntry`, `ScanIssue` | Validate the root, apply selection rules, walk directories, and collect sorted file entries and diagnostics | `hashing` |
| `hashing.py` | `hash_file`, `Digest`, `DirectoryStamp`, `FileReadError`, `UnstableFileError` | Hash regular files in chunks and detect observable replacements or mutations | Python standard library |
| `manifest.py` | `Manifest`, `parse_manifest`, `load_manifest`, `write_manifest`, `ManifestError` | Define v1, validate reference data, and replace the saved manifest atomically | `scanner`, `hashing` |
| `verification.py` | `verify_backup`, `reconcile`, `VerificationItem`, `VerificationReport` | Reapply filters, classify backup paths, and calculate the result and counts | `scanner`, `manifest` |
| `comparison.py` | `compare_manifests`, `ComparisonItem`, `ComparisonReport`, `SnapshotScope` | Compare saved entries and flag partial snapshots or different rule sets | `scanner`, `manifest` |
| `reporting.py` | `render_scan`, `render_verification`, `render_comparison` | Convert models into text or JSON strings | `comparison`, `manifest`, `scanner`, `verification` |

## Model ownership

- `Digest` carries the byte count and SHA-256 of an inspected file. `FileEntry` adds the relative path for persistence and comparison.
- `Inventory` holds the current file map, observed directory paths, and diagnostics. Directory observations help identify a file replaced by a directory. The manifest persists file entries, rules, and diagnostics.
- `ScanIssue` holds a path, issue kind, message, and `subtree` flag. The flag lets verification distinguish a missing file from a file whose parent could not be inspected.
- `VerificationReport` contains the backup outcomes and diagnostics from both scans. `ComparisonReport` contains the saved-snapshot outcomes and the selected scope of each manifest.

## Filesystem and output ownership

`scanner` lists and inspects directory entries, and `hashing` opens regular files for reading. `manifest` reads and writes saved manifests. `verification.reconcile` and `comparison.compare_manifests` work on in-memory inputs. `reporting` returns strings, and `cli` writes them to stdout or emits command errors on stderr.

See the [low-level design](low-level-design.md) for validation order, failure handling, and classification precedence. See the [test plan](test-plan.md) for the test modules that exercise each component.
