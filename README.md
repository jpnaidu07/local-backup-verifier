# Local Backup Verifier

Check that a local backup still contains every expected file with the right contents. Capture a SHA-256 manifest from a known-good folder, then verify a copy on another drive or compare two saved manifests. Verification opens backup files for reading and leaves the folder contents intact.

## Install on Ubuntu

Requires Python 3.10 or later. The CLI uses the Python standard library at runtime.

```bash
sudo apt update
sudo apt install python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
backup-verifier --help
```

You can also run `python -m backup_verifier` after installation.

## Commands

```sh
backup-verifier scan ./original --output original.json
backup-verifier verify ./backup --manifest original.json
backup-verifier diff january.json february.json
backup-verifier verify ./backup --manifest original.json --format json
```

`scan` writes or replaces a manifest outside the source folder after hashing its files. Use a destination whose parent directory already exists. If files cannot be read or change during hashing, the manifest includes the successful entries and diagnostic details. The command reports a partial scan and exits with status 1.

`verify` uses the size and SHA-256 hash of each expected file. It reports `matching`, `missing`, `changed`, `extra`, and `unreadable` paths. A file changing during hashing appears as `unreadable` with reason `unstable`. A directory found in place of a file appears as `changed`. When a directory cannot be inspected, expected files beneath it appear as `unreadable`. Directory errors also appear in the diagnostics, including when no expected files fall beneath the directory.

`diff` compares recorded paths and fingerprints and reports `added`, `removed`, `modified`, and `unchanged` entries. If scan filters differ or a manifest is partial, interpret additions and removals in light of the reported scope. A moved file appears at its old and new paths.

Every command supports `--format json`. Reports go to stdout. Operational and usage errors go to stderr. JSON verification and comparison reports have a `summary` with counts and a `files` array in relative-path order. Each row includes its status and the recorded size and hash wherever available. `issues` on verification describes the backup scan, while `reference_issues` comes from the source manifest.

| Status | `scan` | `verify` | `diff` |
| --- | --- | --- | --- |
| `0` | Complete manifest saved | Every selected file matches and both sides could be inspected | Same recorded files and filters, both scans complete |
| `1` | Partial manifest saved | File discrepancies, inspection failures, or a partial reference | Differences in files or filters, or a partial snapshot |
| `2` | Command, folder, or output error | Command, folder, or manifest error | Command or manifest error |

## Filters and file selection

```sh
backup-verifier scan ./original -o reports.json --include '*.csv' --exclude 'archive/**'
backup-verifier verify ./backup -m reports.json
```

- `--include` and `--exclude` are repeatable and case-sensitive. Exclusions take precedence and prune matching directories. Includes select files. With no includes, all regular files are selected.
- Patterns without `/` match names at any depth. Patterns with `/` match from the scan root. A `*` matches within one path component, `?` matches one character, `[abc]` matches a set, and a whole `**` component matches zero or more path components.
- Verification reuses the reference filters for both expected and extra files. Keep the reference manifest and any redirected report outside the folder being verified so they stay out of its inventory.
- Symbolic links and Windows junctions are recorded as skipped. A link in place of an expected file or directory prevents verification of the affected file paths.

## Manifest and trust

A manifest is UTF-8 JSON. Version 1 requires `version`, `hash_algorithm`, and `files`. Each file entry has `path`, `size` in bytes, and a lowercase hexadecimal `sha256`. Paths use `/` separators and retain their original case. Optional `filters` and `issues` describe selection and scan diagnostics. The CLI writes both fields on every scan.

Keep the reference manifest somewhere you trust. Verification checks against the hashes in that manifest. Authenticating the manifest requires a separate source of trust. Run scans against a stable folder when possible. The CLI checks file identity and metadata during hashing, and walks the tree one entry at a time. It verifies regular-file paths and contents, with empty directories, permissions, timestamps, and link targets outside the manifest's scope.

## Reproduce the demo

Run these commands in Bash after installing the CLI.

```bash
demo_dir=$(mktemp -d)
cd "$demo_dir"
mkdir -p original/reports
printf 'month,total\njanuary,10\n' > original/reports/january.csv
printf 'month,total\nfebruary,20\n' > original/reports/february.csv
printf 'Exported reports\n' > original/notes.txt

backup-verifier scan original -o original.json
cp -a original backup
backup-verifier verify backup -m original.json

printf 'month,total\njanuary,99\n' > backup/reports/january.csv
rm backup/reports/february.csv
printf 'Scratch file\n' > backup/scratch.txt

backup-verifier verify backup -m original.json
backup-verifier verify backup -m original.json --format json
echo "$?"
```

After the edits, each report shows one matching, one missing, one changed, and one extra file. Both verification commands exit with status `1`.

## Tests

```sh
python -m unittest discover -s tests -v
```

GitHub Actions runs the test suite on Ubuntu. The end-to-end test checks the demo in both output formats.

## Design specifications

- [High-level design](docs/specs/high-level-design.md) covers the workflows, architecture, and trust boundary.
- [Low-level design](docs/specs/low-level-design.md) describes the algorithms and how errors affect results.
- [Component specification](docs/specs/components.md) maps module interfaces, dependencies, and tests.
- [CLI and data contracts](docs/specs/cli-and-data-contracts.md) defines flags, exit codes, manifest v1, and report fields.
- [Test plan](docs/specs/test-plan.md) maps acceptance criteria to Ubuntu checks.
