"""Read, validate, and atomically write the versioned manifest format."""

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .hashing import os_error_message
from .scanner import (
    ISSUE_KINDS,
    FileEntry,
    Inventory,
    ScanIssue,
    ScanRules,
    is_manifest_path,
    is_relative_path,
)

VERSION = 1
HASH_ALGORITHM = "sha256"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ManifestError(Exception):
    """A manifest could not be read, validated, or saved."""


@dataclass(frozen=True)
class Manifest:
    """Recorded file fingerprints and the scope of the scan that produced them."""

    files: tuple[FileEntry, ...]
    rules: ScanRules = ScanRules()
    issues: tuple[ScanIssue, ...] = ()

    @classmethod
    def from_inventory(cls, inventory: Inventory, rules: ScanRules) -> "Manifest":
        return cls(tuple(inventory.files.values()), rules, tuple(inventory.issues))

    @property
    def complete(self) -> bool:
        return not any(issue.incomplete for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "hash_algorithm": HASH_ALGORITHM,
            "filters": {
                "include": list(self.rules.include),
                "exclude": list(self.rules.exclude),
            },
            "files": [asdict(entry) for entry in self.files],
            "issues": [asdict(issue) for issue in self.issues],
        }


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{location} must be an object")
    return value


def _array(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{location} must be an array")
    return value


def _required(data: dict[str, Any], field: str, location: str) -> Any:
    if field not in data:
        raise ManifestError(f"{location} is missing required field {field!r}")
    return data[field]


def _parse_rules(value: Any) -> ScanRules:
    filters = _object(value, "filters")
    patterns = {}
    for field in ("include", "exclude"):
        values = _array(filters.get(field, []), f"filters.{field}")
        if not all(isinstance(pattern, str) for pattern in values):
            raise ManifestError(f"filters.{field} must contain only strings")
        patterns[field] = tuple(values)
    try:
        return ScanRules(**patterns)
    except ValueError as error:
        raise ManifestError(f"filters: {error}") from error


def _parse_files(value: Any) -> tuple[FileEntry, ...]:
    files: dict[str, FileEntry] = {}
    for index, item in enumerate(_array(value, "files")):
        location = f"files[{index}]"
        data = _object(item, location)
        path = _required(data, "path", location)
        size = _required(data, "size", location)
        sha256 = _required(data, "sha256", location)
        if not isinstance(path, str) or not is_manifest_path(path):
            raise ManifestError(f"{location}.path must be a relative path with '/' separators")
        if path in files:
            raise ManifestError(f"{location}.path is a duplicate path: {path!r}")
        if type(size) is not int or size < 0:
            raise ManifestError(f"{location}.size must be a nonnegative integer")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise ManifestError(
                f"{location}.sha256 must contain 64 lowercase hexadecimal characters"
            )
        files[path] = FileEntry(path, size, sha256)

    for path in files:
        parent, _, _ = path.rpartition("/")
        while parent:
            if parent in files:
                raise ManifestError(
                    f"files defines {parent!r} as both a file and a parent of {path!r}"
                )
            parent, _, _ = parent.rpartition("/")
    return tuple(files[path] for path in sorted(files))


def _parse_issues(value: Any) -> tuple[ScanIssue, ...]:
    issues = []
    for index, item in enumerate(_array(value, "issues")):
        location = f"issues[{index}]"
        data = _object(item, location)
        path = _required(data, "path", location)
        kind = _required(data, "kind", location)
        message = _required(data, "message", location)
        subtree = data.get("subtree", False)
        if not isinstance(path, str) or not is_relative_path(path):
            raise ManifestError(f"{location}.path must be a relative path")
        if not isinstance(kind, str) or kind not in ISSUE_KINDS:
            raise ManifestError(f"{location}.kind must be one of {', '.join(sorted(ISSUE_KINDS))}")
        if not isinstance(message, str) or not message:
            raise ManifestError(f"{location}.message must be a nonempty string")
        if type(subtree) is not bool:
            raise ManifestError(f"{location}.subtree must be a boolean")
        issues.append(ScanIssue(path, kind, message, subtree))
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.kind, issue.message)))


def parse_manifest(value: Any) -> Manifest:
    """Validate JSON-compatible data before using it as a reference."""
    data = _object(value, "manifest")
    version = _required(data, "version", "manifest")
    if type(version) is not int or version != VERSION:
        raise ManifestError(f"unsupported manifest version {version!r}; expected {VERSION}")
    algorithm = _required(data, "hash_algorithm", "manifest")
    if algorithm != HASH_ALGORITHM:
        raise ManifestError(f"unsupported hash algorithm {algorithm!r}; expected {HASH_ALGORITHM}")

    rules = _parse_rules(data.get("filters", {}))
    files = _parse_files(_required(data, "files", "manifest"))
    for entry in files:
        parts = tuple(entry.path.split("/"))
        if not rules.included(parts) or any(
            rules.excluded(parts[:depth]) for depth in range(1, len(parts) + 1)
        ):
            raise ManifestError(f"file {entry.path!r} is outside the manifest filters")
    issues = _parse_issues(data.get("issues", []))
    return Manifest(files, rules, issues)


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ManifestError(f"invalid JSON constant {value!r}")


def load_manifest(path: Path) -> Manifest:
    """Load UTF-8 JSON and attach the input filename to validation errors."""
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(
                source,
                object_pairs_hook=_unique_keys,
                parse_constant=_invalid_constant,
            )
        return parse_manifest(data)
    except OSError as error:
        raise ManifestError(
            f"cannot read manifest {str(path)!r}: {os_error_message(error)}"
        ) from error
    except (ValueError, RecursionError, ManifestError) as error:
        raise ManifestError(f"invalid manifest {str(path)!r}: {error}") from error


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Replace the output only after the new manifest has been written in full."""
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".backup-verifier-",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(manifest.to_dict(), output, indent=2, ensure_ascii=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise ManifestError(
            f"cannot write manifest {str(path)!r}: {os_error_message(error)}"
        ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Keep the original write error visible to the caller.
                pass
