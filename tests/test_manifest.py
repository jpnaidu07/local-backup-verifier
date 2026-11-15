import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_verifier.manifest import Manifest, ManifestError, load_manifest, parse_manifest, write_manifest
from backup_verifier.scanner import FileEntry, ScanIssue, ScanRules

SHA = "a" * 64


def entry(path="report.csv", **fields):
    return {"path": path, "size": 3, "sha256": SHA, **fields}


def document(*entries):
    return {"version": 1, "hash_algorithm": "sha256", "files": list(entries)}


class ManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "manifest.json"

    def test_round_trip_filters_unicode_and_diagnostics(self):
        manifest = Manifest(
            (FileEntry("donn\u00e9es/r\u00e9sum\u00e9.csv", 3, SHA),),
            ScanRules(("*.csv",), ("cache",)),
            (ScanIssue("broken.csv", "unreadable", "access denied"),),
        )
        write_manifest(self.path, manifest)
        self.assertEqual(load_manifest(self.path), manifest)
        self.assertFalse(manifest.complete)
        on_disk = json.loads(self.path.read_bytes())
        self.assertEqual(on_disk["version"], 1)
        self.assertEqual(on_disk["hash_algorithm"], "sha256")

    def test_minimal_manifest_and_entry_sort_order(self):
        result = parse_manifest(document(entry("z.csv"), entry("a.csv")))
        self.assertEqual([item.path for item in result.files], ["a.csv", "z.csv"])
        self.assertEqual(result.rules, ScanRules())
        self.assertTrue(result.complete)

    def test_unsupported_versions_and_algorithms(self):
        for version in (False, True, 2, "1", 1.0):
            with self.subTest(version=version), self.assertRaisesRegex(ManifestError, "unsupported manifest version"):
                parse_manifest({**document(), "version": version})
        with self.assertRaisesRegex(ManifestError, "unsupported hash algorithm"):
            parse_manifest({**document(), "hash_algorithm": "md5"})

    def test_required_fields_and_entry_types(self):
        for value, message in (
            ([], "manifest must be an object"),
            ({"version": 1}, "missing required field 'hash_algorithm'"),
            (document(None), "must be an object"),
            (document({"path": "data"}), "missing required field 'size'"),
            (document(entry(size=True)), "nonnegative integer"),
            (document(entry(size=-1)), "nonnegative integer"),
            (document(entry(sha256="z" * 64)), "64 lowercase hexadecimal"),
            (document(entry(sha256="F" * 64)), "64 lowercase hexadecimal"),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ManifestError, message):
                parse_manifest(value)

    def test_invalid_relative_paths(self):
        for path in ("", "/a", "a/..", "a/./b", "C:data", "C:/data", "a\\b", "a//b", "a/", "a\0b"):
            with self.subTest(path=path), self.assertRaisesRegex(ManifestError, "relative path"):
                parse_manifest(document(entry(path)))

    def test_duplicate_paths_and_conflicting_parents(self):
        with self.assertRaisesRegex(ManifestError, "duplicate path"):
            parse_manifest(document(entry(), entry()))
        with self.assertRaisesRegex(ManifestError, "both a file and a parent"):
            parse_manifest(document(entry("a/child"), entry("a")))

    def test_filters_cannot_hide_expected_entries(self):
        with self.assertRaisesRegex(ManifestError, "outside the manifest filters"):
            parse_manifest({**document(entry("cache/item.csv")), "filters": {"exclude": ["cache"]}})
        with self.assertRaisesRegex(ManifestError, "filters.include must contain only strings"):
            parse_manifest({**document(), "filters": {"include": [3]}})

    def test_invalid_diagnostics(self):
        issue = {"path": "item.csv", "kind": "unstable", "message": "changed"}
        for overrides, message in (
            ({"kind": []}, "issues.*kind"),
            ({"path": "../escape"}, "issues.*path"),
            ({"subtree": 1}, "issues.*subtree"),
            ({"message": ""}, "issues.*message"),
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ManifestError, message):
                parse_manifest({**document(), "issues": [{**issue, **overrides}]})

    def test_malformed_json_reports_filename(self):
        for data, message in (
            (b"{broken", "Expecting property name"),
            (b'{"version":1,"version":1}', "duplicate JSON field"),
            (b'{"version":NaN}', "invalid JSON constant"),
            (b"\xff", "utf-8"),
        ):
            self.path.write_bytes(data)
            with self.subTest(data=data), self.assertRaisesRegex(ManifestError, f"manifest.json.*{message}"):
                load_manifest(self.path)

    def test_successful_replace_and_cleanup_on_failure(self):
        previous = parse_manifest(document(entry("previous.csv")))
        updated = parse_manifest(document(entry("updated.csv")))
        write_manifest(self.path, previous)
        with patch("backup_verifier.manifest.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(ManifestError, "cannot write manifest.*locked"):
                write_manifest(self.path, updated)
        self.assertEqual(load_manifest(self.path), previous)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        write_manifest(self.path, updated)
        self.assertEqual(load_manifest(self.path), updated)
