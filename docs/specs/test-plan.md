# Test plan

Ubuntu is the release target. GitHub Actions installs the package, checks the installed `backup-verifier --help` entry point, and runs the suite on Python 3.10 and 3.14. The tests use temporary directories and synthetic inputs. They require local filesystem access and no external service credentials.

```bash
python -m pip install .
python -m unittest discover -s tests -v
```

| Test module | Main coverage |
| --- | --- |
| [`test_scanner.py`](../../tests/test_scanner.py) | Lexical order, relative paths, glob semantics, pruning, skipped symlinks, Linux FIFOs, invalid roots, and partial or changing directories |
| [`test_hashing.py`](../../tests/test_hashing.py) | Multi-chunk binary input, empty file, read errors, replacements, concurrent changes, and file growth |
| [`test_manifest.py`](../../tests/test_manifest.py) | v1 round trip, Unicode, schema errors, duplicate keys or paths, invalid relative paths and filters, and preservation of the previous manifest when a replacement fails |
| [`test_verification.py`](../../tests/test_verification.py) | Same-size corruption, read-only verification, filtered extras, directory substitutions, unreadable subtrees, partial references, and skipped links blocking expected paths |
| [`test_comparison.py`](../../tests/test_comparison.py) | All four diff status values, sorted rows, scope differences, and partial snapshots |
| [`test_cli.py`](../../tests/test_cli.py) | Subprocess invocations, output and exit status, partial manifests, invalid invocations, protected scan destinations, and the end-to-end backup demo |

## Acceptance demo

1. Create `reports/january.csv`, `reports/february.csv`, and `notes.txt` in a synthetic source. Scan it and require exit `0` with three manifest entries.
2. Copy the folder to a separate backup. Verify and require exit `0` with three matching paths.
3. Modify January without changing its size, remove February, keep the notes, and add `scratch.txt`.
4. Verify in text and JSON. Require exit `1` for both. The ordered file results are `notes.txt` matching, `reports/february.csv` missing, `reports/january.csv` changed, and `scratch.txt` extra. JSON reports one of each status and zero unreadable paths.
5. Confirm the saved manifest still matches the original bytes.

The README includes a [manual Bash demo](../../README.md#reproduce-the-demo) for the same workflow. Test fixtures should remain synthetic and account-independent.
