# Low-level design

## Scan and inventory

1. `scan_root()` inspects the supplied root with `lstat()`, requires a real directory, resolves its path, and checks that the resolved directory has the same identity. An invalid root raises `ScanError`.
2. For `scan`, the CLI resolves the manifest output's parent and uses the final path component as the destination name. The destination must be outside the chosen source root. This prevents the manifest from entering its own inventory or replacing a source file.
3. `walk_tree()` keeps a stack of directory paths and their ancestor identities. Each listing uses `os.scandir()`, sorts children by name, checks the ancestor identities before and after listing, and checks the directory's modification time across the listing. Root listing failures abort the command. Below the root, listing failures become issues for the affected subtree and walking continues.
4. For each child, apply exclusions before inspecting metadata. A directory matching an exclusion is pruned. Record skipped links. Queue regular directories with representable paths and apply includes to regular-file candidates. Metadata failures, unsupported selected entries, and failed hashes produce issues. A child entry that disappears after listing produces an `unstable` issue.
5. Return files sorted by relative path and diagnostics sorted by path and kind. `Manifest.from_inventory()` retains file fingerprints, scan rules, and issues.

`ScanRules` compares strings case-sensitively. A pattern with no slash matches the last component at any depth. For a root-relative pattern, matching advances across path components. A complete `**` component can advance zero or more positions. Include patterns are combined with OR, and an empty include list selects all candidates. Exclusions override includes.

## File hashing

`hash_file()` checks the observed ancestors and file path, then opens the regular file with `os.open()` in read-only mode. On Ubuntu it adds `O_NOFOLLOW` and `O_NONBLOCK` so an entry replaced by a symlink or FIFO fails the file checks without reading the replacement's contents. The descriptor's metadata must agree with the pre-read observation. Ubuntu checks device, inode, file type, size, and modification and change times in nanoseconds.

The read loop feeds `hashlib.sha256()` with chunks of up to 1 MiB. It reads at most one byte beyond the initial file size to detect growth. Early EOF, an extra byte, or a differing metadata check yields `UnstableFileError`. After reading, it checks descriptor metadata, closes the descriptor, and rechecks the ancestors and file path. Paths that vanish or are replaced after observation are unstable. Other OS errors become `FileReadError`.

This bounds the memory used for an individual file's content. Directory listings, relative paths, inventories, manifests, and reports are held in memory. Ancestor checks add metadata work proportional to path depth. Sorting inventories and report paths adds work as the number of entries grows.

## Manifest persistence

`load_manifest()` decodes UTF-8 JSON, rejects duplicate object fields and nonstandard numeric constants, then validates v1 and its entries. It checks for duplicate file paths, paths that are also parents of other file paths, and entries contradicted by the stored filters. It sorts accepted files and diagnostics. Failures include the manifest filename and the validation detail.

`write_manifest()` serializes to a temporary sibling of the destination, flushes and synchronizes the file with `fsync()`, closes it, and replaces the destination with `os.replace()`. An error before replacement leaves the previous destination intact and triggers best-effort cleanup of the temporary file. Directory synchronization and power-loss durability are outside the write contract.

## Verification reconciliation

The verifier loads the manifest first and scans the backup with its filters. It indexes actual files and diagnostics by relative path. For each expected path, the first applicable row determines its outcome.

| Precedence | Backup observation | Result |
| --- | --- | --- |
| 1 | A fingerprint exists and agrees in size and SHA-256 | `matching` |
| 2 | A fingerprint exists and differs in size or SHA-256 | `changed` with reason `content` |
| 3 | A diagnostic applies to the exact path or to its nearest blocking ancestor marked `subtree` | `unreadable` with the diagnostic kind as `reason` |
| 4 | The path is present as a directory | `changed` with reason `type` |
| 5 | No applicable observation exists | `missing` |

An observed file with no expected entry is `extra`. An incomplete backup diagnostic at an untracked path can create an `unreadable` row when that path has neither a directory observation nor a file fingerprint. Known directory issues remain diagnostics and can affect multiple expected paths. A skipped link becomes a finding when it blocks an expected path. Skipped links encountered elsewhere are informational.

Rows sort by path and count once toward their status. Success requires a complete reference, no incomplete backup issues, and all reported rows to be matching. A backup issue can make verification fail even if the issue contributes only a diagnostic.

## Snapshot comparison

`compare_manifests()` indexes the two file collections by path and iterates their sorted union. Only the later snapshot contains an `added` path. Only the earlier snapshot contains a `removed` path. Shared paths are `modified` when their size or hash differs and `unchanged` when both agree.

The report provides each snapshot's filters and issues. It flags changed filters by comparing the sets of include and exclude patterns independently. Pattern order and duplicate strings have no effect on that flag. A partial snapshot or a different rule set yields a finding even when the recorded entries agree. File statuses always describe the recorded entries, so apparent additions and removals from scans with different coverage need context.
