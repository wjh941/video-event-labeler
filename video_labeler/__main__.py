"""Command-line entry point for CSV compatibility operations."""

from __future__ import annotations

import argparse
from pathlib import Path

from .storage.csv_adapter import export_csv, import_csv
from .storage.sqlite_store import SQLiteStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m video_labeler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    imp = subparsers.add_parser("import-csv")
    imp.add_argument("--csv", required=True, type=Path)
    imp.add_argument("--video-root", required=True, type=Path)
    imp.add_argument("--db", required=True, type=Path)
    exp = subparsers.add_parser("export-csv")
    exp.add_argument("--csv", required=True, type=Path)
    exp.add_argument("--video-root", required=True, type=Path)
    exp.add_argument("--db", required=True, type=Path)
    args = parser.parse_args(argv)
    store = SQLiteStore(args.db)
    try:
        if args.command == "import-csv":
            report = import_csv(args.csv, store, args.video_root)
            print(f"created={report.created} updated={report.updated} skipped={report.skipped} stale={report.stale} errors={len(report.errors)}")
            return 1 if report.errors else 0
        report = export_csv(store, args.csv, args.video_root)
        print(f"exported={report.sample_count} path={report.path}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
