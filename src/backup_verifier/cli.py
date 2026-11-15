"""Command-line entry points and exit status handling."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .comparison import compare_manifests
from .hashing import os_error_message
from .manifest import Manifest, ManifestError, load_manifest, write_manifest
from .reporting import render_comparison, render_scan, render_verification
from .scanner import ScanError, ScanRules, scan_root, walk_tree
from .verification import verify_backup

SUCCESS = 0
FINDINGS = 1
ERROR = 2


def _report_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="stdout report format (default: text)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup-verifier",
        description="Create and verify SHA-256 manifests for local backups.",
        epilog="Exit status: 0 success, 1 findings or partial scan, 2 command or input error.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="capture a manifest from a known-good folder")
    scan.add_argument("folder", type=Path, help="source folder to scan")
    scan.add_argument(
        "--output",
        "-o",
        required=True,
        type=Path,
        help="manifest file outside the source folder",
    )
    scan.add_argument(
        "--include",
        metavar="GLOB",
        action="append",
        default=[],
        help="include matching files (repeatable)",
    )
    scan.add_argument(
        "--exclude",
        metavar="GLOB",
        action="append",
        default=[],
        help="exclude matching files or directories (repeatable)",
    )
    _report_option(scan)
    scan.set_defaults(run=_scan)

    verify = commands.add_parser("verify", help="check a backup folder against a manifest")
    verify.add_argument("folder", type=Path, help="backup folder to verify")
    verify.add_argument(
        "--manifest", "-m", required=True, type=Path, help="trusted reference manifest"
    )
    _report_option(verify)
    verify.set_defaults(run=_verify)

    diff = commands.add_parser("diff", help="compare two saved manifests")
    diff.add_argument("before", type=Path, help="earlier manifest")
    diff.add_argument("after", type=Path, help="later manifest")
    _report_option(diff)
    diff.set_defaults(run=_diff)
    return parser


def _scan(args: argparse.Namespace) -> int:
    rules = ScanRules(tuple(args.include), tuple(args.exclude))
    root = scan_root(args.folder)
    try:
        # os.replace replaces the named directory entry, including a symlink.
        target = args.output.parent.resolve() / args.output.name
    except (OSError, RuntimeError) as error:
        reason = os_error_message(error) if isinstance(error, OSError) else str(error)
        raise ManifestError(f"cannot resolve output {str(args.output)!r}: {reason}") from error
    if target.is_relative_to(root.path):
        raise ManifestError("choose a manifest output outside the source folder")

    inventory = walk_tree(root, rules)
    manifest = Manifest.from_inventory(inventory, rules)
    write_manifest(target, manifest)
    print(render_scan(manifest, args.output, args.format))
    return SUCCESS if manifest.complete else FINDINGS


def _verify(args: argparse.Namespace) -> int:
    report = verify_backup(args.folder, load_manifest(args.manifest))
    print(render_verification(report, args.format))
    return SUCCESS if report.ok else FINDINGS


def _diff(args: argparse.Namespace) -> int:
    report = compare_manifests(load_manifest(args.before), load_manifest(args.after))
    print(render_comparison(report, args.format))
    return SUCCESS if report.ok else FINDINGS


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.run(args)
    except (ManifestError, ScanError, ValueError) as error:
        print(f"backup-verifier: {error}", file=sys.stderr)
        return ERROR
    except KeyboardInterrupt:
        print("backup-verifier: interrupted", file=sys.stderr)
        return 130
