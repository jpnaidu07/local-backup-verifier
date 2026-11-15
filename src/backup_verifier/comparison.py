"""Compare the file entries and scan scope of two saved snapshots."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from .manifest import Manifest
from .scanner import FileEntry, ScanIssue, ScanRules

Status = Literal["added", "removed", "modified", "unchanged"]
STATUSES: tuple[Status, ...] = ("added", "removed", "modified", "unchanged")


@dataclass(frozen=True)
class ComparisonItem:
    path: str
    status: Status
    before: FileEntry | None = None
    after: FileEntry | None = None


@dataclass(frozen=True)
class SnapshotScope:
    rules: ScanRules
    issues: tuple[ScanIssue, ...]

    @property
    def complete(self) -> bool:
        return not any(issue.incomplete for issue in self.issues)


@dataclass(frozen=True)
class ComparisonReport:
    """Recorded differences together with the scope of both input manifests."""

    files: tuple[ComparisonItem, ...]
    before: SnapshotScope
    after: SnapshotScope

    @property
    def summary(self) -> dict[str, int]:
        counts = Counter(item.status for item in self.files)
        return {status: counts[status] for status in STATUSES}

    @property
    def filters_changed(self) -> bool:
        return (
            set(self.before.rules.include) != set(self.after.rules.include)
            or set(self.before.rules.exclude) != set(self.after.rules.exclude)
        )

    @property
    def ok(self) -> bool:
        return (
            self.before.complete
            and self.after.complete
            and not self.filters_changed
            and all(item.status == "unchanged" for item in self.files)
        )


def compare_manifests(before: Manifest, after: Manifest) -> ComparisonReport:
    """Compare recorded entries by relative path in sorted order."""
    old_files = {entry.path: entry for entry in before.files}
    new_files = {entry.path: entry for entry in after.files}
    items = []
    for path in sorted(old_files.keys() | new_files.keys()):
        old_entry = old_files.get(path)
        new_entry = new_files.get(path)
        if old_entry is None:
            status: Status = "added"
        elif new_entry is None:
            status = "removed"
        elif old_entry == new_entry:
            status = "unchanged"
        else:
            status = "modified"
        items.append(ComparisonItem(path, status, old_entry, new_entry))

    return ComparisonReport(
        files=tuple(items),
        before=SnapshotScope(before.rules, before.issues),
        after=SnapshotScope(after.rules, after.issues),
    )
