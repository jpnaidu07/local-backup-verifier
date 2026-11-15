import unittest

from backup_verifier.comparison import compare_manifests
from backup_verifier.manifest import Manifest
from backup_verifier.scanner import FileEntry, ScanIssue, ScanRules


class ComparisonTests(unittest.TestCase):
    def test_classifies_and_sorts_all_four_statuses(self):
        original = FileEntry("modify.txt", 3, "a" * 64)
        updated = FileEntry("modify.txt", 3, "b" * 64)
        unchanged = FileEntry("same.txt", 3, "c" * 64)
        removed = FileEntry("z.txt", 3, "d" * 64)
        added = FileEntry("a.txt", 3, "d" * 64)
        report = compare_manifests(
            Manifest((removed, unchanged, original)), Manifest((unchanged, added, updated))
        )
        self.assertEqual(report.summary, {"added": 1, "removed": 1, "modified": 1, "unchanged": 1})
        self.assertEqual([(item.path, item.status) for item in report.files], [
            ("a.txt", "added"), ("modify.txt", "modified"), ("same.txt", "unchanged"), ("z.txt", "removed"),
        ])
        self.assertEqual((report.files[1].before, report.files[1].after), (original, updated))
        self.assertFalse(report.ok)

    def test_size_difference_is_also_modified(self):
        before = Manifest((FileEntry("data", 1, "a" * 64),))
        after = Manifest((FileEntry("data", 2, "a" * 64),))
        self.assertEqual(compare_manifests(before, after).files[0].status, "modified")

    def test_empty_identical_manifests_are_equal(self):
        report = compare_manifests(Manifest(()), Manifest(()))
        self.assertTrue(report.ok)
        self.assertEqual(report.files, ())

    def test_partial_or_different_scope_is_a_finding(self):
        before = Manifest(())
        scoped = Manifest((), ScanRules(("*.csv",)))
        partial = Manifest((), issues=(ScanIssue("file.txt", "unreadable", "denied"),))
        different_filters = compare_manifests(before, scoped)
        self.assertTrue(different_filters.filters_changed)
        self.assertFalse(different_filters.ok)
        incomplete = compare_manifests(before, partial)
        self.assertTrue(incomplete.before.complete)
        self.assertFalse(incomplete.after.complete)
        self.assertFalse(incomplete.ok)

    def test_filter_order_and_duplicate_patterns_do_not_affect_scope(self):
        before = Manifest((), ScanRules(("*.txt", "*.csv"), ("archive",)))
        after = Manifest((), ScanRules(("*.csv", "*.txt", "*.csv"), ("archive", "archive")))
        self.assertTrue(compare_manifests(before, after).ok)
