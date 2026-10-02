"""按级别筛选 JSONL 日志行的命令行实现。"""

import argparse
import json
import sys

SUPPORTED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def normalize_level(value):
    """忽略大小写和首尾空白，返回规范化级别；无法识别时返回 None。"""
    normalized = value.strip().upper()
    if normalized in SUPPORTED_LEVELS:
        return normalized
    return None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m log_viewer",
        description="读取一份 JSONL 日志文件，按级别筛选并输出匹配的原始行。",
    )
    parser.add_argument("path", help="要读取的 JSONL 日志文件路径（UTF-8 编码）")
    parser.add_argument(
        "--level",
        required=True,
        help="筛选级别：DEBUG、INFO、WARNING、ERROR、CRITICAL（忽略大小写和首尾空白）",
    )
    return parser


def read_lines(path):
    """读取文件全部物理行（去掉行末换行符）。读取失败时抛出异常。"""
    with open(path, "r", encoding="utf-8", newline=None) as handle:
        return handle.read().split("\n")


def iter_matches(lines, target):
    """逐行处理，产生 (行号, 原始行) 匹配项；无效行向标准错误输出警告。"""
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue  # 空白行直接跳过，仍占用行号
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            record = None
        level = record.get("level") if isinstance(record, dict) else None
        normalized = normalize_level(level) if isinstance(level, str) else None
        if normalized is None:
            print(f"第 {lineno} 行：无效日志，已跳过", file=sys.stderr)
            continue
        if normalized == target:
            yield lineno, raw


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    target = normalize_level(args.level)
    if target is None:
        parser.error(
            "无效的筛选级别：{!r}，支持 {}".format(args.level, "、".join(SUPPORTED_LEVELS))
        )

    try:
        lines = read_lines(args.path)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"文件读取失败：{args.path}（{exc}）", file=sys.stderr)
        return 2

    # 文件末尾的换行符会产生一个空的尾部元素，它不是物理行
    if lines and lines[-1] == "":
        lines.pop()

    out = sys.stdout
    for lineno, raw in iter_matches(lines, target):
        out.write(f"{lineno}\t{raw}\n")
    return 0
