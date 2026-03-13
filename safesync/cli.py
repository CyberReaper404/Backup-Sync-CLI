from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .database import StateDatabase
from .engine import SyncEngine
from .safety import default_state_dir


def build_parser() -> argparse.ArgumentParser:
    """Monta a interface de linha de comando da aplicação."""
    parser = argparse.ArgumentParser(
        prog="backup",
        description="Safe folder backup and synchronization CLI with SQLite history.",
    )
    parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Directory that stores the SQLite history and versioned blobs.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Synchronize files from source to destination.")
    sync_parser.add_argument("source", help="Source directory to read from.")
    sync_parser.add_argument("destination", help="Destination directory to update.")
    sync_parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Ignore files or folders by name or glob pattern. Repeat to add multiple rules.",
    )
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Write verified changes to disk. Without this flag, sync runs in preview mode.",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the sync without writing files or storing versioned blobs.",
    )
    sync_parser.add_argument(
        "--report",
        help="Optional path to save the JSON report for this run.",
    )

    history_parser = subparsers.add_parser("history", help="Show recent synchronization runs.")
    history_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of runs to show.",
    )

    report_parser = subparsers.add_parser("report", help="Export a stored run as JSON.")
    report_parser.add_argument("run_id", type=int, help="Run id to export.")
    report_parser.add_argument(
        "--output",
        required=True,
        help="Where to save the JSON report.",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore a saved run snapshot into a target directory.",
    )
    restore_parser.add_argument("run_id", type=int, help="Run id to restore.")
    restore_parser.add_argument("output", help="Directory that will receive the restored snapshot.")
    restore_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace files in the output directory if they already exist.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    database = StateDatabase(Path(args.state_dir).resolve())
    engine = SyncEngine(database)

    try:
        if args.command == "sync":
            # A escrita só acontece com --apply; sem isso a execução vira preview seguro.
            summary = engine.sync(
                source_dir=Path(args.source),
                destination_dir=Path(args.destination),
                ignore_patterns=args.ignore,
                dry_run=not args.apply,
                report_path=Path(args.report) if args.report else None,
            )
            print(
                "\n".join(
                    [
                        f"Run: {summary.run_id}",
                        f"Status: {summary.status}",
                        f"Mode: {'dry-run' if summary.dry_run else 'live'}",
                        f"Source: {summary.source_dir}",
                        f"Destination: {summary.destination_dir}",
                        f"Scanned: {summary.files_scanned}",
                        f"Copied: {summary.files_copied}",
                        f"Updated: {summary.files_updated}",
                        f"Skipped: {summary.files_skipped}",
                        f"Bytes copied: {summary.bytes_copied}",
                    ]
                )
            )
            if args.report:
                print(f"Report saved to: {Path(args.report).resolve()}")
            return 0

        if args.command == "history":
            database.initialize()
            runs = database.list_runs(limit=args.limit)
            if not runs:
                print("No runs found.")
                return 0

            for run in runs:
                mode = "dry-run" if run["dry_run"] else "live"
                print(
                    f"[{run['id']}] {run['status']} | {mode} | "
                    f"{run['files_scanned']} scanned | {run['files_copied']} copied | "
                    f"{run['files_updated']} updated | {run['files_skipped']} skipped | "
                    f"{run['started_at']}"
                )
            return 0

        if args.command == "report":
            report_path = engine.write_report(args.run_id, Path(args.output))
            print(f"Report saved to: {report_path}")
            return 0

        if args.command == "restore":
            restored_path = engine.restore(
                run_id=args.run_id,
                output_dir=Path(args.output),
                overwrite=args.overwrite,
            )
            print(f"Run {args.run_id} restored into: {restored_path}")
            return 0

        parser.print_help()
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
