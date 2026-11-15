import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backup_verifier.cli import main
from backup_verifier.hashing import FileReadError
from backup_verifier.manifest import load_manifest


class CLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "original"
        self.source.mkdir()
        self.backup = self.root / "backup"
        self.manifest = self.root / "original.json"

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "backup_verifier", *(str(arg) for arg in args)],
            check=False, capture_output=True, text=True, timeout=20,
        )

    def test_full_backup_workflow_in_text_and_json(self):
        (self.source / "reports").mkdir()
        (self.source / "reports/january.csv").write_bytes(b"month,total\njan,10\n")
        (self.source / "reports/february.csv").write_bytes(b"month,total\nfeb,20\n")
        (self.source / "notes.txt").write_bytes(b"source exports\n")

        scan = self.cli("scan", self.source, "--output", self.manifest)
        self.assertEqual(scan.returncode, 0, scan.stderr + scan.stdout)
        self.assertIn("3 files", scan.stdout)
        original_manifest = self.manifest.read_bytes()
        shutil.copytree(self.source, self.backup)
        clean = self.cli("verify", self.backup, "--manifest", self.manifest)
        self.assertEqual(clean.returncode, 0, clean.stderr + clean.stdout)
        self.assertIn("3 matching", clean.stdout)

        (self.backup / "reports/january.csv").write_bytes(b"month,total\njan,99\n")
        (self.backup / "reports/february.csv").unlink()
        (self.backup / "scratch.txt").write_bytes(b"extra\n")
        text = self.cli("verify", self.backup, "--manifest", self.manifest)
        self.assertEqual(text.returncode, 1, text.stderr + text.stdout)
        self.assertIn("1 matching, 1 missing, 1 changed, 1 extra, 0 unreadable", text.stdout)
        self.assertIn("MATCHING notes.txt", text.stdout)
        self.assertIn("MISSING reports/february.csv", text.stdout)
        self.assertIn("CHANGED reports/january.csv", text.stdout)
        self.assertIn("EXTRA scratch.txt", text.stdout)

        structured = self.cli("verify", self.backup, "--manifest", self.manifest, "--format", "json")
        self.assertEqual(structured.returncode, 1, structured.stderr + structured.stdout)
        report = json.loads(structured.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"], {"matching": 1, "missing": 1, "changed": 1, "extra": 1, "unreadable": 0})
        self.assertEqual([(item["path"], item["status"]) for item in report["files"]], [
            ("notes.txt", "matching"), ("reports/february.csv", "missing"),
            ("reports/january.csv", "changed"), ("scratch.txt", "extra"),
        ])
        self.assertEqual(self.manifest.read_bytes(), original_manifest)

    def test_diff_reports_distinct_and_identical_snapshots(self):
        (self.source / "report.csv").write_bytes(b"old")
        before = self.cli("scan", self.source, "-o", self.manifest, "--format", "json")
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(json.loads(before.stdout)["summary"], {"files": 1, "issues": 0})

        (self.source / "report.csv").write_bytes(b"new")
        after_path = self.root / "after.json"
        self.assertEqual(self.cli("scan", self.source, "-o", after_path).returncode, 0)
        different = self.cli("diff", self.manifest, after_path, "--format", "json")
        self.assertEqual(different.returncode, 1)
        self.assertEqual(json.loads(different.stdout)["summary"]["modified"], 1)
        same = self.cli("diff", after_path, after_path)
        self.assertEqual(same.returncode, 0)
        self.assertIn("1 unchanged", same.stdout)

    def test_usage_and_operational_errors_go_to_stderr(self):
        malformed = self.root / "malformed.json"
        malformed.write_bytes(b'{"version": 5}')
        for args, expected in (
            ((), "usage:"),
            (("scan", self.source), "--output"),
            (("scan", self.root / "missing", "-o", self.manifest), "cannot open folder"),
            (("scan", self.source, "-o", self.root / "absent" / "manifest.json"), "cannot write manifest"),
            (("verify", self.source, "-m", malformed, "--format", "json"), "unsupported manifest version 5"),
            (("verify", self.source, "-m", self.manifest), "cannot read manifest"),
        ):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_output_inside_source_is_rejected_without_overwriting_file(self):
        existing = self.source / "existing.txt"
        existing.write_bytes(b"keep me")
        result = self.cli("scan", self.source, "-o", existing)
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside the source folder", result.stderr)
        self.assertEqual(existing.read_bytes(), b"keep me")

    def test_output_symlink_inside_source_is_rejected(self):
        link = self.source / "manifest.json"
        try:
            link.symlink_to(self.manifest)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        result = self.cli("scan", self.source, "-o", link)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(link.is_symlink())
        self.assertFalse(self.manifest.exists())

    def test_partial_scan_writes_diagnostics_with_exit_status_one(self):
        (self.source / "unreadable.txt").touch()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch(
            "backup_verifier.scanner.hash_file", side_effect=FileReadError("access denied")
        ):
            status = main(["scan", str(self.source), "-o", str(self.manifest), "--format", "json"])
        self.assertEqual(status, 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["ok"])
        self.assertEqual(report["issues"][0]["path"], "unreadable.txt")
        self.assertFalse(load_manifest(self.manifest).complete)
