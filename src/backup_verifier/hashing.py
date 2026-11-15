"""Read regular files in bounded chunks and detect common concurrent changes."""

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Digest:
    size: int
    sha256: str


class FileReadError(Exception):
    """No reliable digest could be produced for a file."""

    kind = "unreadable"


class UnstableFileError(FileReadError):
    """The file or its path changed during the scan."""

    kind = "unstable"


def is_link(info: os.stat_result) -> bool:
    """Treat Windows directory junctions like symbolic links."""
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None) in (
        stat.IO_REPARSE_TAG_MOUNT_POINT,
        stat.IO_REPARSE_TAG_SYMLINK,
    )


def os_error_message(error: OSError) -> str:
    # OSError.strerror gives the cause without leaking an absolute source path.
    return error.strerror or str(error)


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _file_stamp(info: os.stat_result, *, same_source: bool) -> tuple[int, ...]:
    stamp = (*_identity(info), info.st_size, info.st_mtime_ns)
    # Windows can report different creation times through lstat and fstat.
    if same_source or os.name != "nt":
        return (*stamp, info.st_ctime_ns)
    return stamp


@dataclass(frozen=True)
class DirectoryStamp:
    """Identity of a directory observed when walking to a file."""

    path: Path
    info: os.stat_result

    def check(self) -> os.stat_result:
        try:
            current = self.path.lstat()
        except (FileNotFoundError, NotADirectoryError) as error:
            raise UnstableFileError("a parent directory disappeared") from error
        if is_link(current) or _identity(current) != _identity(self.info):
            raise UnstableFileError("a parent directory was replaced")
        return current


def _check_file(
    expected: os.stat_result,
    current: os.stat_result,
    *,
    same_source: bool = True,
) -> None:
    if (
        is_link(current)
        or not stat.S_ISREG(current.st_mode)
        or _file_stamp(expected, same_source=same_source)
        != _file_stamp(current, same_source=same_source)
    ):
        raise UnstableFileError("file was changed or replaced")


def _read_file(path: Path, expected: os.stat_result, chunk_size: int) -> Digest:
    # O_NOFOLLOW avoids opening a replaced symlink on platforms that support it.
    # O_NONBLOCK avoids waiting on a FIFO that replaced a regular file.
    flags = os.O_RDONLY
    for flag in ("O_BINARY", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag, 0)

    checksum = hashlib.sha256()
    byte_count = 0
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _check_file(expected, opened, same_source=False)
        while chunk := os.read(descriptor, min(chunk_size, expected.st_size - byte_count + 1)):
            byte_count += len(chunk)
            if byte_count > expected.st_size:
                raise UnstableFileError("bytes read exceed the recorded file size")
            checksum.update(chunk)
        _check_file(opened, os.fstat(descriptor))
    finally:
        os.close(descriptor)

    if byte_count != expected.st_size:
        raise UnstableFileError("bytes read differ from the recorded file size")
    return Digest(byte_count, checksum.hexdigest())


def hash_file(
    path: Path,
    *,
    observed: os.stat_result | None = None,
    parents: tuple[DirectoryStamp, ...] = (),
    chunk_size: int = CHUNK_SIZE,
) -> Digest:
    """Hash a regular file and confirm its metadata and path still agree."""
    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")

    try:
        for parent in parents:
            parent.check()
        before = path.lstat()
        if observed is not None:
            _check_file(observed, before)
        elif is_link(before) or not stat.S_ISREG(before.st_mode):
            raise FileReadError("path is not a regular file")

        observed = before
        digest = _read_file(path, before, chunk_size)
        for parent in parents:
            parent.check()
        _check_file(before, path.lstat())
    except OSError as error:
        vanished_or_replaced = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}
        if observed is not None and error.errno in vanished_or_replaced:
            raise UnstableFileError("file disappeared or was replaced") from error
        raise FileReadError(os_error_message(error)) from error
    return digest
