# CLI and data contracts

## Command syntax

```text
backup-verifier scan <source-dir> --output <manifest-file> [--include <glob>] [--exclude <glob>] [--format text|json]
backup-verifier verify <backup-dir> --manifest <manifest-file> [--format text|json]
backup-verifier diff <before-manifest> <after-manifest> [--format text|json]
```

Angle-bracketed values are placeholders. Quote real paths containing spaces and quote globs to pass them through the shell. `-o` and `-m` are aliases for `--output` and `--manifest`. Repeat `--include` or `--exclude` to build up the filter lists. The output parent directory must already exist. The default report format is `text`.

| Exit code | `scan` | `verify` | `diff` |
| --- | --- | --- | --- |
| `0` | Complete manifest written | All selected files match and inspections are complete | Recorded entries agree and both complete manifests have the same filter sets |
| `1` | Partial manifest written | Discrepancy, incomplete backup inspection, or partial reference | Entries or filter sets differ, or either manifest is partial |
| `2` | Invalid invocation, root failure, or output failure | Invalid invocation, manifest failure, or root failure | Invalid invocation or manifest failure |

Reports go to stdout. Usage and operational errors go to stderr, including in JSON mode. The command handler returns `130` for a keyboard interruption.

## Manifest v1

```json
{
  "version": 1,
  "hash_algorithm": "sha256",
  "filters": {"include": ["*.txt"], "exclude": ["cache/**"]},
  "files": [
    {
      "path": "reports/empty.txt",
      "size": 0,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  ],
  "issues": []
}
```

The example records the digest of a zero-byte fixture. The CLI writes UTF-8 JSON with a trailing newline and includes all top-level fields shown above.

| Field | Reader requirement |
| --- | --- |
| `version` | Required integer equal to `1` |
| `hash_algorithm` | Required string equal to `sha256` |
| `filters` | Optional object. `include` and `exclude` are arrays of relative glob strings and each defaults to an empty array. |
| `files` | Required array of file records. Each has `path`, a nonnegative integer `size` in bytes, and a 64-character lowercase hexadecimal `sha256`. |
| `issues` | Optional array of diagnostic records. Defaults to an empty array. |

A file path is case-sensitive and relative, with slash-separated components. Empty components, `.`, `..`, NUL characters, backslashes, drive prefixes, duplicate paths, and file paths used as parents of other file paths are invalid. File entries must satisfy the stored filters. The reader ignores unknown fields and rejects duplicate JSON object keys.

A diagnostic carries a nonempty `message`, a relative `path` without empty components, `.` or `..` components, or NUL characters, and a `kind` of `unreadable`, `unstable`, `unsupported`, or `symlink`. It may include a boolean `subtree`, which defaults to `false`. Diagnostic paths can name entries that fail the stricter file-path syntax. A scan is complete when every issue, if any, is a skipped link.

## JSON reports

All reports include a `command` string, an `ok` boolean that corresponds to exit code `0`, and a `summary` object. File rows sort by relative path. A fingerprint in a report carries `size` and `sha256`; the row carries its `path` separately.

| Command | Additional top-level fields | Summary fields |
| --- | --- | --- |
| `scan` | `manifest` output path and `issues` | `files`, `issues` |
| `verify` | `reference_complete`, `files`, `issues` for the backup, and `reference_issues` | `matching`, `missing`, `changed`, `extra`, `unreadable` |
| `diff` | `filters_changed`, `files`, and `before` and `after` scope objects, each with `complete`, `filters`, and `issues` | `added`, `removed`, `modified`, `unchanged` |

A `verify` file row contains `path`, `status`, `expected`, `actual`, `reason`, and `message`. The last four fields may be `null`. A `diff` file row contains `path`, `status`, `before`, and `after`. A missing snapshot side has a `null` fingerprint. Every status is present in `summary`, including those with zero counts. Totals count file rows; the issue arrays can also describe incomplete directory listings.
