"""Render stable JSON reports and concise reports for a terminal."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from .comparison import ComparisonReport, SnapshotScope
from .manifest import Manifest
from .scanner import FileEntry, ScanIssue
from .verification import VerificationReport

ReportFormat = Literal["text", "json"]


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=True)


def _fingerprint(entry: FileEntry | None) -> dict[str, str | int] | None:
    if entry is None:
        return None
    return {"size": entry.size, "sha256": entry.sha256}


def _summary(counts: dict[str, int]) -> str:
    return ", ".join(f"{count} {status}" for status, count in counts.items())


def _display(value: str) -> str:
    return value if value.isprintable() else repr(value)


def _issue_lines(issues: tuple[ScanIssue, ...], label: str) -> list[str]:
    return [
        f"  {label} {issue.kind.upper()} {_display(issue.path)} ({_display(issue.message)})"
        for issue in issues
    ]


def render_scan(manifest: Manifest, output: Path, format: ReportFormat) -> str:
    if format == "json":
        return _json({
            "command": "scan",
            "manifest": str(output),
            "ok": manifest.complete,
            "summary": {"files": len(manifest.files), "issues": len(manifest.issues)},
            "issues": [asdict(issue) for issue in manifest.issues],
        })
    state = "Manifest" if manifest.complete else "Partial manifest"
    return "\n".join([
        f"{state} written to {_display(str(output))} ({len(manifest.files)} files).",
        *_issue_lines(manifest.issues, "Source:"),
    ])


def render_verification(report: VerificationReport, format: ReportFormat) -> str:
    if format == "json":
        return _json({
            "command": "verify",
            "ok": report.ok,
            "reference_complete": report.reference_complete,
            "summary": report.summary,
            "files": [
                {
                    "path": item.path,
                    "status": item.status,
                    "expected": _fingerprint(item.expected),
                    "actual": _fingerprint(item.actual),
                    "reason": item.reason,
                    "message": item.message,
                }
                for item in report.files
            ],
            "issues": [asdict(issue) for issue in report.issues],
            "reference_issues": [asdict(issue) for issue in report.reference_issues],
        })

    result = "OK" if report.ok else "FAILED"
    lines = [f"Verification {result} ({_summary(report.summary)})."]
    if not report.reference_complete:
        lines.extend([
            "The reference manifest is partial.",
            "Rescan a known-good source for a complete baseline.",
        ])
    for item in report.files:
        detail = f" ({_display(item.message)})" if item.message else ""
        if item.status == "unreadable":
            detail = f" [{item.reason}]{detail}"
        lines.append(f"  {item.status.upper()} {_display(item.path)}{detail}")
    # File issues already printed in the rows only need to appear once in text.
    unreadable_paths = {
        item.path for item in report.files if item.status == "unreadable"
    }
    remaining_issues = tuple(
        issue for issue in report.issues if issue.path not in unreadable_paths
    )
    lines.extend(_issue_lines(remaining_issues, "Backup:"))
    lines.extend(_issue_lines(report.reference_issues, "Reference:"))
    return "\n".join(lines)


def _scope_payload(scope: SnapshotScope) -> dict[str, Any]:
    return {
        "complete": scope.complete,
        "filters": {
            "include": list(scope.rules.include),
            "exclude": list(scope.rules.exclude),
        },
        "issues": [asdict(issue) for issue in scope.issues],
    }


def render_comparison(report: ComparisonReport, format: ReportFormat) -> str:
    if format == "json":
        return _json({
            "command": "diff",
            "ok": report.ok,
            "filters_changed": report.filters_changed,
            "summary": report.summary,
            "before": _scope_payload(report.before),
            "after": _scope_payload(report.after),
            "files": [
                {
                    "path": item.path,
                    "status": item.status,
                    "before": _fingerprint(item.before),
                    "after": _fingerprint(item.after),
                }
                for item in report.files
            ],
        })

    lines = [f"Snapshot comparison ({_summary(report.summary)})."]
    if report.filters_changed:
        lines.append("Scan filters differ between snapshots.")
    if not report.before.complete or not report.after.complete:
        lines.append("At least one snapshot came from a partial scan.")
    lines.extend(
        f"  {item.status.upper()} {_display(item.path)}"
        for item in report.files
    )
    lines.extend(_issue_lines(report.before.issues, "Before:"))
    lines.extend(_issue_lines(report.after.issues, "After:"))
    return "\n".join(lines)
