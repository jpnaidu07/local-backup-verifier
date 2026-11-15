import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backup_verifier.hashing import UnstableFileError, is_link
from backup_verifier.scanner import ScanError, ScanRules, scan_tree


class ScannerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, relative_path, content=b"data"):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_path_order_is_lexical_and_relative(self):
        self.write("zeta.txt")
        self.write("folder/omicron.csv")
        self.write("A.txt")
        self.write("folder/alpha.csv")
        result = scan_tree(self.root)
        self.assertEqual(list(result.files), ["A.txt", "folder/alpha.csv", "folder/omicron.csv", "zeta.txt"])
        self.assertEqual(result.issues, [])

    def test_include_and_exclude_apply_to_the_same_scope(self):
        for path in (
            "reports/root.csv", "reports/nested/data.csv", "reports/nested/upper.CSV",
            "reports/nested/cache/omit.csv", "elsewhere/root.csv", "notes.txt",
        ):
            self.write(path)
        result = scan_tree(self.root, ScanRules(("reports/**/*.csv", "notes.*"), ("cache",)))
        self.assertEqual(list(result.files), ["notes.txt", "reports/nested/data.csv", "reports/root.csv"])

    def test_glob_parts_and_directory_exclusions(self):
        self.write("reports/2025/q1.csv")
        self.write("reports/2026/q2.csv")
        self.write("reports/2026/deep/q3.csv")
        self.write("archive/current.csv")
        rules = ScanRules(("reports/202[56]/q?.csv", "archive/*.csv"), ("archive/**",))
        result = scan_tree(self.root, rules)
        self.assertEqual(list(result.files), ["reports/2025/q1.csv", "reports/2026/q2.csv"])
        self.assertNotIn("archive", result.directories)

    def test_invalid_globs_are_rejected(self):
        for pattern in ("", "/reports", "../*.csv", "C:/data", "reports\\*.csv", "reports//*.csv"):
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, "invalid glob"):
                ScanRules((pattern,))

    def test_skips_symlink_files_and_directories(self):
        self.write("real/item.txt")
        try:
            (self.root / "linked").symlink_to("real", target_is_directory=True)
            (self.root / "broken").symlink_to("absent.txt")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        result = scan_tree(self.root)
        self.assertEqual(list(result.files), ["real/item.txt"])
        self.assertEqual([(issue.path, issue.kind) for issue in result.issues], [
            ("broken", "symlink"), ("linked", "symlink"),
        ])

    @unittest.skipUnless(os.name == "nt", "Windows directory junction behavior")
    def test_junction_is_recognized_as_a_link(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=stat.IO_REPARSE_TAG_MOUNT_POINT)
        self.assertTrue(is_link(info))

    def test_a_file_failure_preserves_other_results(self):
        self.write("unstable.txt")
        self.write("ok.txt")
        from backup_verifier.scanner import hash_file

        def hash_or_fail(path, **kwargs):
            if path.name == "unstable.txt":
                raise UnstableFileError("file was changed or replaced")
            return hash_file(path, **kwargs)

        with patch("backup_verifier.scanner.hash_file", side_effect=hash_or_fail):
            result = scan_tree(self.root)
        self.assertEqual(list(result.files), ["ok.txt"])
        self.assertEqual(result.issues[0].kind, "unstable")

    def test_directory_failure_is_reported_with_subtree_scope(self):
        self.write("blocked/expected.txt")
        original_scandir = os.scandir

        def deny_directory(path):
            if Path(path).name == "blocked":
                raise PermissionError("access denied")
            return original_scandir(path)

        with patch("backup_verifier.scanner.os.scandir", side_effect=deny_directory):
            result = scan_tree(self.root)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].path, "blocked")
        self.assertTrue(result.issues[0].subtree)

    def test_directory_changed_during_listing_is_unstable(self):
        self.write("reports/january.csv")
        original_scandir = os.scandir

        def change_directory(path):
            if Path(path).name == "reports":
                info = os.stat(path)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns - 2_000_000_000))
            return original_scandir(path)

        with patch("backup_verifier.scanner.os.scandir", side_effect=change_directory):
            result = scan_tree(self.root)
        self.assertEqual(result.files, {})
        self.assertEqual([(issue.path, issue.kind) for issue in result.issues], [
            ("reports", "unstable"),
        ])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires a POSIX FIFO")
    def test_special_file_is_reported_without_opening_it(self):
        os.mkfifo(self.root / "pipe")
        result = scan_tree(self.root)
        self.assertEqual(result.files, {})
        self.assertEqual(result.issues[0].kind, "unsupported")

    def test_non_directory_root_is_fatal(self):
        self.write("file.txt")
        with self.assertRaises(ScanError):
            scan_tree(self.root / "file.txt")
        with self.assertRaises(ScanError):
            scan_tree(self.root / "missing")
