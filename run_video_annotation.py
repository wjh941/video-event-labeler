#!/usr/bin/env python3
"""Run the event-labeling stage followed by person-identity labeling."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import video_event_labeler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="先标注行为事件，再使用同一份 CSV 标注人物身份"
    )
    parser.add_argument("--video-root", type=Path, required=True, help="视频根目录")
    parser.add_argument("--csv", type=Path, help="清单路径，默认视频根目录下的 video_labeler_manifest.csv")
    parser.add_argument("--person-only", action="store_true", help="跳过行为标注，直接进入人物标注")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--event-port", type=int, default=8765, help="行为标注起始端口")
    parser.add_argument("--person-port", type=int, default=8865, help="人物标注起始端口")
    parser.add_argument("--db", type=Path, help="SQLite database path")
    return parser


def run_stage(command: list[str]) -> int:
    process = subprocess.Popen(command)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return 0


def main() -> int:
    args = build_parser().parse_args()
    root = args.video_root.expanduser().resolve()
    if not root.is_dir():
        print(f"视频根目录不存在: {root}", file=sys.stderr)
        return 2
    csv_path = (args.csv or root / "video_labeler_manifest.csv").expanduser().resolve()
    if csv_path.parent != root:
        print("组合启动器要求 CSV 位于视频根目录下", file=sys.stderr)
        return 2

    try:
        video_event_labeler.import_video_directory(root, csv_path.name)
    except (OSError, ValueError) as error:
        print(f"生成 CSV 失败: {error}", file=sys.stderr)
        return 2

    common = ["--video-root", str(root), "--csv", str(csv_path)]
    db_path = (args.db or root / "dataset.db").expanduser().resolve()
    common.extend(["--db", str(db_path)])
    if args.no_browser:
        common.append("--no-browser")

    if not args.person_only:
        print("第一阶段：行为与事件时间标注。完成后在终端按 Ctrl+C，进入人物标注。")
        result = run_stage(
            [sys.executable, str(Path(__file__).with_name("video_event_labeler.py")), *common, "--port", str(args.event_port)]
        )
        if result != 0:
            return result

    print("第二阶段：人物身份属性标注。完成后在终端按 Ctrl+C 结束。")
    return run_stage(
        [sys.executable, str(Path(__file__).with_name("person_identity_labeler.py")), *common, "--port", str(args.person_port)]
    )


if __name__ == "__main__":
    raise SystemExit(main())
