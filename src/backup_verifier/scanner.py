"""Walk local directory trees and capture portable file paths and hashes."""

import os
import stat
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PureWindowsPath

from .hashing import (
    DirectoryStamp,
    FileReadError,
    UnstableFileError,
    hash_file,
    is_link,
    os_error_message,
)

ISSUE_KINDS = frozenset({"unreadable", "unstable", "symlink", "unsupported"})


def is_relative_path(value: str) -> bool:
    """Check the slash-delimited relative path syntax used by inventories."""
    return "\0" not in value and all(
        part not in {"", ".", ".."} for part in value.split("/")
    )


def is_manifest_path(value: str) -> bool:
    return is_relative_path(value) and "\\" not in value and not PureWindowsPath(value).drive


def _match_path(path: tuple[str, ...], pattern: str) -> bool:
    if "/" not in pattern:
        return fnmatchcase(path[-1], pattern)

    # Each index is a position in the path reached by matching a prefix of
    # the pattern. A whole ** segment can consume zero or more components.
    positions = {0}
    for part in pattern.split("/"):
        if part == "**":
            positions = set(range(min(positions), len(path) + 1))
        else:
            positions = {
                index + 1
                for index in positions
                if index < len(path) and fnmatchcase(path[index], part)
            }
        if not positions:
            return False
    return len(path) in positions


@dataclass(frozen=True)
class ScanRules:
    """Select regular files with includes and prune matching paths with excludes."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for pattern in (*self.include, *self.exclude):
            if not is_manifest_path(pattern):
                raise ValueError(
                    f"invalid glob {pattern!r}. Use a relative path with '/' separators"
                )

    def excluded(self, path: tuple[str, ...]) -> bool:
        return any(_match_path(path, pattern) for pattern in self.exclude)

    def included(self, path: tuple[str, ...]) -> bool:
        return not self.include or any(_match_path(path, pattern) for pattern in self.include)


@dataclass(frozen=True)
class FileEntry:
    """Fingerprint for one regular file at a relative path."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ScanIssue:
    """An inspection problem or a skipped link, possibly affecting descendants."""

    path: str
    kind: str
    message: str
    subtree: bool = False

    @property
    def incomplete(self) -> bool:
        return self.kind != "symlink"


@dataclass
class Inventory:
    """Observed files, directories, and diagnostics from one filesystem walk."""

    files: dict[str, FileEntry] = field(default_factory=dict)
    directories: set[str] = field(default_factory=set)
    issues: list[ScanIssue] = field(default_factory=list)


class ScanError(Exception):
    """The requested scan root cannot be walked."""


def scan_root(path: Path) -> DirectoryStamp:
    try:
        info = path.lstat()
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ScanError(f"scan requires a real directory: {str(path)!r}")
        root = DirectoryStamp(path.resolve(strict=True), info)
        root.check()
        return root
    except (OSError, FileReadError, RuntimeError) as error:
        reason = os_error_message(error) if isinstance(error, OSError) else str(error)
        raise ScanError(f"cannot open folder {str(path)!r}: {reason}") from error


def _list_directory(
    parents: tuple[DirectoryStamp, ...],
) -> list[os.DirEntry[str]]:
    parent = parents[-1]
    for directory in parents[:-1]:
        directory.check()
    before = parent.check()
    with os.scandir(parent.path) as entries:
        result = sorted(entries, key=lambda entry: entry.name)
    for directory in parents[:-1]:
        directory.check()
    after = parent.check()
    if before.st_mtime_ns != after.st_mtime_ns:
        raise UnstableFileError("directory entries changed while listing")
    return result


def _scan_entry(
    entry: os.DirEntry[str],
    parts: tuple[str, ...],
    parents: tuple[DirectoryStamp, ...],
    rules: ScanRules,
    inventory: Inventory,
) -> DirectoryStamp | None:
    path = "/".join(parts)
    if rules.excluded(parts):
        return None
    entry_path = Path(entry.path)
    try:
        # DirEntry.stat does not supply device and inode IDs on Windows.
        info = entry_path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        inventory.issues.append(ScanIssue(
            path, "unstable", "entry disappeared after listing", subtree=True
        ))
        return None
    except OSError as error:
        inventory.issues.append(ScanIssue(
            path, "unreadable", os_error_message(error), subtree=True
        ))
        return None

    if is_link(info):
        inventory.issues.append(ScanIssue(
            path, "symlink", "symbolic link or junction skipped", subtree=True
        ))
    elif not is_manifest_path(path):
        if stat.S_ISDIR(info.st_mode) or rules.included(parts):
            inventory.issues.append(ScanIssue(
                path, "unsupported", "path cannot be stored in a manifest", subtree=True
            ))
    elif stat.S_ISDIR(info.st_mode):
        inventory.directories.add(path)
        return DirectoryStamp(entry_path, info)
    elif rules.included(parts):
        if not stat.S_ISREG(info.st_mode):
            inventory.issues.append(ScanIssue(
                path, "unsupported", "path is not a regular file"
            ))
            return None
        try:
            digest = hash_file(entry_path, observed=info, parents=parents)
        except FileReadError as error:
            inventory.issues.append(ScanIssue(path, error.kind, str(error)))
        else:
            inventory.files[path] = FileEntry(path, digest.size, digest.sha256)
    return None


def walk_tree(root: DirectoryStamp, rules: ScanRules = ScanRules()) -> Inventory:
    """Return a deterministic inventory, keeping failures local to each path."""
    inventory = Inventory()
    pending = [((), (root,))]
    while pending:
        parts, parents = pending.pop()
        try:
            entries = _list_directory(parents)
        except (OSError, FileReadError) as error:
            reason = os_error_message(error) if isinstance(error, OSError) else str(error)
            if not parts:
                raise ScanError(
                    f"cannot list folder {str(root.path)!r}: {reason}"
                ) from error
            kind = error.kind if isinstance(error, FileReadError) else "unreadable"
            inventory.issues.append(ScanIssue("/".join(parts), kind, reason, True))
            continue

        subdirectories = []
        for entry in entries:
            child_parts = (*parts, entry.name)
            directory = _scan_entry(entry, child_parts, parents, rules, inventory)
            if directory is not None:
                subdirectories.append((child_parts, (*parents, directory)))
        pending.extend(reversed(subdirectories))

    inventory.files = dict(sorted(inventory.files.items()))
    inventory.issues.sort(key=lambda issue: (issue.path, issue.kind))
    return inventory


def scan_tree(path: Path, rules: ScanRules = ScanRules()) -> Inventory:
    return walk_tree(scan_root(path), rules)
