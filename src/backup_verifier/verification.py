"""Reconcile a manifest with an inventory of the backup folder."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .manifest import Manifest
from .scanner import FileEntry, Inventory, ScanIssue, scan_tree

Status = Literal["matching", "missing", "changed", "extra", "unreadable"]
STATUSES: tuple[Status, ...] = ("matching", "missing", "changed", "extra", "unreadable")


@dataclass(frozen=True)
class VerificationItem:
    path: str
    status: Status
    expected: FileEntry | None = None
    actual: FileEntry | None = None
    reason: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    """File outcomes and diagnostics for the backup and its reference scan."""

    files: tuple[VerificationItem, ...]
    issues: tuple[ScanIssue, ...]
    reference_issues: tuple[ScanIssue, ...]

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(item.status for item in self.files)
        return {status: counts[status] for status in STATUSES}

    @property
    def reference_complete(self) -> bool:
        return not any(issue.incomplete for issue in self.reference_issues)

    @property
    def ok(self) -> bool:
        return (
            self.reference_complete
            and not any(issue.incomplete for issue in self.issues)
            and all(item.status == "matching" for item in self.files)
        )


def _blocking_issue(path: str, issues: dict[str, ScanIssue]) -> ScanIssue | None:
    if path in issues:
        return issues[path]
    parent, _, _ = path.rpartition("/")
    while parent:
        issue = issues.get(parent)
        if issue is not None and issue.subtree:
            return issue
        parent, _, _ = parent.rpartition("/")
    return None


def _unreadable(path: str, issue: ScanIssue, expected: FileEntry | None) -> VerificationItem:
    message = issue.message
    if issue.path != path:
        message = f"could not inspect {issue.path!r}: {message}"
    return VerificationItem(
        path,
        "unreadable",
        expected=expected,
        reason=issue.kind,
        message=message,
    )


def _classify_expected(
    entry: FileEntry, actual: Inventory, issues: dict[str, ScanIssue]
) -> VerificationItem:
    path = entry.path
    found = actual.files.get(path)
    if found is not None:
        if found == entry:
            return VerificationItem(path, "matching", entry, found)
        return VerificationItem(
            path,
            "changed",
            expected=entry,
            actual=found,
            reason="content",
            message="size or SHA-256 hash differs",
        )

    issue = _blocking_issue(path, issues)
    if issue is not None:
        return _unreadable(path, issue, entry)
    if path in actual.directories:
        return VerificationItem(
            path,
            "changed",
            expected=entry,
            reason="type",
            message="found a directory in place of a file",
        )
    return VerificationItem(path, "missing", entry)


def reconcile(manifest: Manifest, actual: Inventory) -> VerificationReport:
    """Assign one status per expected file or additional observed file path."""
    expected = {entry.path: entry for entry in manifest.files}
    issues = {issue.path: issue for issue in actual.issues}
    files = [_classify_expected(entry, actual, issues) for entry in manifest.files]

    for path, entry in actual.files.items():
        if path not in expected:
            files.append(VerificationItem(path, "extra", actual=entry))
    for path, issue in issues.items():
        if (
            issue.incomplete
            and path not in expected
            and path not in actual.directories
            and path not in actual.files
        ):
            files.append(_unreadable(path, issue, None))

    return VerificationReport(
        files=tuple(sorted(files, key=lambda item: item.path)),
        issues=tuple(actual.issues),
        reference_issues=manifest.issues,
    )


def verify_backup(path: Path, manifest: Manifest) -> VerificationReport:
    """Scan a backup against the same selection rules used by the reference."""
    return reconcile(manifest, scan_tree(path, manifest.rules))
