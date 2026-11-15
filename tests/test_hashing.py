import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backup_verifier.hashing import (
    Digest,
    DirectoryStamp,
    FileReadError,
    UnstableFileError,
    hash_file,
)


class HashingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / "sample.bin"

    def test_binary_data_is_hashed_in_chunks(self):
        payload = bytes(range(256)) * 40
        self.file.write_bytes(payload)
        with patch("backup_verifier.hashing.os.read", wraps=os.read) as read:
            digest = hash_file(self.file, chunk_size=113)
        self.assertGreater(read.call_count, 1)
        self.assertEqual(read.call_args_list[0].args[1], 113)
        self.assertTrue(all(0 < call.args[1] <= 113 for call in read.call_args_list))
        self.assertEqual(digest, Digest(len(payload), hashlib.sha256(payload).hexdigest()))

    def test_empty_file_has_a_digest(self):
        self.file.touch()
        self.assertEqual(hash_file(self.file), Digest(0, hashlib.sha256(b"").hexdigest()))

    def test_missing_file_and_directory_are_unreadable(self):
        for path in (self.file, self.root):
            with self.subTest(path=path), self.assertRaises(FileReadError) as caught:
                hash_file(path)
            self.assertEqual(caught.exception.kind, "unreadable")

    def test_open_failure_is_unreadable(self):
        self.file.write_bytes(b"data")
        with patch("backup_verifier.hashing.os.open", side_effect=PermissionError("access denied")):
            with self.assertRaisesRegex(FileReadError, "access denied"):
                hash_file(self.file)

    def test_file_replaced_since_listing_is_unstable(self):
        self.file.write_bytes(b"old")
        observed = self.file.lstat()
        replacement = self.root / "replacement"
        replacement.write_bytes(b"new")
        replacement.replace(self.file)
        with self.assertRaises(UnstableFileError):
            hash_file(self.file, observed=observed)

    def test_disappearing_parent_is_unstable(self):
        parent = self.root / "parent"
        parent.mkdir()
        observed = DirectoryStamp(parent, parent.lstat())
        parent.rename(self.root / "elsewhere")
        with self.assertRaisesRegex(UnstableFileError, "parent directory disappeared"):
            hash_file(parent / "data", parents=(observed,))

    def test_detects_file_changed_during_chunk_read(self):
        self.file.write_bytes(b"abcdef")
        original_read = os.read

        def modify_after_read(descriptor, length):
            data = original_read(descriptor, length)
            info = self.file.stat()
            os.utime(self.file, ns=(info.st_atime_ns, info.st_mtime_ns - 2_000_000_000))
            return data

        with patch("backup_verifier.hashing.os.read", side_effect=modify_after_read):
            with self.assertRaisesRegex(UnstableFileError, "changed or replaced"):
                hash_file(self.file, chunk_size=4)

    def test_stops_reading_if_more_bytes_appear(self):
        self.file.write_bytes(b"abc")
        with patch("backup_verifier.hashing.os.read", side_effect=(b"abc", b"d")) as read:
            with self.assertRaisesRegex(UnstableFileError, "exceed the recorded file size"):
                hash_file(self.file)
        self.assertEqual([call.args[1] for call in read.call_args_list], [4, 1])

    @unittest.skipUnless(os.name == "nt", "Windows file metadata behavior")
    def test_windows_handle_creation_time_can_differ_from_path_creation_time(self):
        self.file.write_bytes(b"data")
        original_fstat = os.fstat

        def report_different_creation_time(descriptor):
            info = original_fstat(descriptor)
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_ino=info.st_ino,
                st_dev=info.st_dev,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
                st_ctime_ns=info.st_ctime_ns + 7_000_000_000,
            )

        with patch("backup_verifier.hashing.os.fstat", side_effect=report_different_creation_time):
            self.assertEqual(hash_file(self.file).sha256, hashlib.sha256(b"data").hexdigest())
