"""Command-line operations for SQLite annotation datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .maintenance import backup_database, check_database
from .media_index import index_media
from .quality import dataset_stats, export_jsonl, validate_dataset
from .storage.csv_adapter import export_csv, import_csv
from .storage.sqlite_store import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m video_labeler")
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import-csv"); imp.add_argument("--csv", required=True, type=Path); imp.add_argument("--video-root", required=True, type=Path); imp.add_argument("--db", required=True, type=Path)
    exp_csv = commands.add_parser("export-csv"); exp_csv.add_argument("--csv", required=True, type=Path); exp_csv.add_argument("--video-root", required=True, type=Path); exp_csv.add_argument("--db", required=True, type=Path)
    validate = commands.add_parser("validate"); validate.add_argument("--db", required=True, type=Path); validate.add_argument("--mode", choices=("draft", "strict"), default="draft")
    stats = commands.add_parser("stats"); stats.add_argument("--db", required=True, type=Path)
    export = commands.add_parser("export"); export.add_argument("--db", required=True, type=Path); export.add_argument("--format", choices=("jsonl",), required=True); export.add_argument("--output", "--path", "--csv", dest="output", required=True, type=Path); export.add_argument("--manifest", type=Path); export.add_argument("--split-seed", default="video-labeler-v1")
    index = commands.add_parser("index-media"); index.add_argument("--db", required=True, type=Path); index.add_argument("--video-root", required=True, type=Path); index.add_argument("--ffprobe", type=Path)
    backup = commands.add_parser("backup-db"); backup.add_argument("--db", required=True, type=Path); backup.add_argument("--output", required=True, type=Path)
    commands.add_parser("check-db").add_argument("--db", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SQLiteStore(args.db)
    try:
        if args.command == "import-csv":
            import_report = import_csv(args.csv, store, args.video_root)
            print(json.dumps({"created": import_report.created, "updated": import_report.updated, "skipped": import_report.skipped, "stale": import_report.stale, "errors": [item.__dict__ for item in import_report.errors]}, ensure_ascii=False))
            return 1 if import_report.errors else 0
        if args.command == "export-csv":
            csv_report = export_csv(store, args.csv, args.video_root)
            print(json.dumps({"path": str(csv_report.path), "sample_count": csv_report.sample_count}, ensure_ascii=False))
            return 0
        if args.command == "validate":
            quality_report = validate_dataset(store, mode=args.mode)
            print(json.dumps(quality_report.to_dict(), ensure_ascii=False, indent=2))
            return 0 if quality_report.ok else 1
        if args.command == "stats":
            print(json.dumps(dataset_stats(store), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "index-media":
            report = index_media(args.video_root, store, ffprobe_path=args.ffprobe)
            print(json.dumps({"scanned": report.scanned, "indexed": report.indexed, "skipped": report.skipped, "errors": report.errors}, ensure_ascii=False))
            return 1 if report.errors else 0
        if args.command == "backup-db":
            output = backup_database(store, args.output)
            print(json.dumps({"path": str(output)}, ensure_ascii=False))
            return 0
        if args.command == "check-db":
            report = check_database(store)
            print(json.dumps({"ok": report.ok, "integrity_check": report.integrity_check, "schema_version": report.schema_version}, ensure_ascii=False))
            return 0 if report.ok else 1
        jsonl_report = export_jsonl(store, args.output, manifest_path=args.manifest, split_seed=args.split_seed)
        print(json.dumps({"path": str(jsonl_report.path), "sample_count": jsonl_report.sample_count}, ensure_ascii=False))
        return 0
    finally:
        store.close()


__all__ = ["build_parser", "main"]
