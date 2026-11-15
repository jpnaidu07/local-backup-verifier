import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_verifier.hashing import FileReadError, UnstableFileError
from backup_verifier.manifest import Manifest
from backup_verifier.scanner import ScanIssue, ScanRules, scan_tree
from backup_verifier.verification import verify_backup


class VerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "source"
        self.backup = root / "backup"
        self.source.mkdir()

    def capture(self, rules=ScanRules()):
        baseline = Manifest.from_inventory(scan_tree(self.source, rules), rules)
        shutil.copytree(self.source, self.backup)
        return baseline

    def test_same_size_changes_are_detected_and_contents_are_unchanged_by_verify(self):
        (self.source / "data.txt").write_bytes(b"alpha")
        manifest = self.capture()
        path = self.backup / "data.txt"
        path.write_bytes(b"omega")
        before = path.stat()
        report = verify_backup(self.backup, manifest)
        self.assertEqual(report.summary["changed"], 1)
        self.assertEqual(report.files[0].actual.size, report.files[0].expected.size)
        self.assertNotEqual(report.files[0].actual.sha256, report.files[0].expected.sha256)
        self.assertEqual(path.read_bytes(), b"omega")
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_filters_apply_to_extra_paths(self):
        (self.source / "track.csv").write_bytes(b"data")
        manifest = self.capture(ScanRules(("*.csv",), ("cache",)))
        (self.backup / "ignore.txt").touch()
        (self.backup / "cache").mkdir()
        (self.backup / "cache" / "omit.csv").touch()
        report = verify_backup(self.backup, manifest)
        self.assertTrue(report.ok)
        self.assertEqual(report.summary["matching"], 1)
        self.assertEqual(report.summary["extra"], 0)

    def test_directory_in_place_of_file_is_changed(self):
        (self.source / "entry").touch()
        manifest = self.capture()
        (self.backup / "entry").unlink()
        (self.backup / "entry").mkdir()
        report = verify_backup(self.backup, manifest)
        self.assertEqual((report.files[0].status, report.files[0].reason), ("changed", "type"))

    def test_unreadable_subtree_preserves_expected_file_status(self):
        (self.source / "blocked").mkdir()
        (self.source / "blocked" / "item.txt").touch()
        (self.source / "notblocked.txt").touch()
        manifest = self.capture()
        original_scandir = os.scandir

        def deny_directory(path):
            if Path(path).name == "blocked":
                raise PermissionError("denied")
            return original_scandir(path)

        with patch("backup_verifier.scanner.os.scandir", side_effect=deny_directory):
            report = verify_backup(self.backup, manifest)
        self.assertEqual(report.summary["missing"], 0)
        self.assertEqual(report.summary["unreadable"], 1)
        self.assertEqual(report.summary["matching"], 1)
        self.assertEqual(report.issues[0].path, "blocked")

    def test_expected_and_untracked_read_failures_have_distinct_reasons(self):
        (self.source / "unstable.txt").touch()
        manifest = self.capture()
        (self.backup / "extra.txt").touch()
        errors = [FileReadError("denied"), UnstableFileError("changed")]
        with patch("backup_verifier.scanner.hash_file", side_effect=errors):
            report = verify_backup(self.backup, manifest)
        self.assertEqual(report.summary["unreadable"], 2)
        self.assertIsNone(report.files[0].expected)
        self.assertEqual([item.reason for item in report.files], ["unreadable", "unstable"])

    def test_partial_reference_cannot_pass_verification(self):
        clean = self.capture()
        partial = Manifest(clean.files, clean.rules, (ScanIssue("data.txt", "unreadable", "denied"),))
        report = verify_backup(self.backup, partial)
        self.assertFalse(report.ok)
        self.assertFalse(report.reference_complete)
        self.assertEqual(report.reference_issues, partial.issues)

    def test_link_replacing_expected_directory_blocks_its_files(self):
        (self.source / "folder").mkdir()
        (self.source / "folder" / "data.txt").touch()
        manifest = self.capture()
        (self.backup / "folder").rename(self.backup / "renamed")
        try:
            (self.backup / "folder").symlink_to("renamed", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        report = verify_backup(self.backup, manifest)
        self.assertEqual(report.files[0].path, "folder/data.txt")
        self.assertEqual((report.files[0].status, report.files[0].reason), ("unreadable", "symlink"))
