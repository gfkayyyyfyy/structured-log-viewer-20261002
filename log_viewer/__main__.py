"""命令行入口：python -m log_viewer <文件路径> --level <级别>"""

import argparse
import sys

from . import SUPPORTED_LEVELS, iter_matches, normalize_level


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m log_viewer",
        description="读取 JSONL 日志文件，按级别筛选并输出匹配的原始行。",
    )
    parser.add_argument("path", help="JSONL 日志文件路径（UTF-8 编码）")
    parser.add_argument(
        "--level",
        required=True,
        help="筛选级别：DEBUG、INFO、WARNING、ERROR、CRITICAL（忽略大小写及首尾空白）",
    )
    parser.add_argument(
        "--request-id",
        default=None,
        help="可选：仅输出顶层 request_id 字段与该值精确相等的记录（区分大小写）",
    )
    args = parser.parse_args(argv)

    if args.request_id is not None and not args.request_id.strip():
        print("参数错误：--request-id 不能为空或全为空白", file=sys.stderr)
        return 2

    level = normalize_level(args.level)
    if level not in SUPPORTED_LEVELS:
        print(
            f"参数错误：--level 必须是以下级别之一：{', '.join(SUPPORTED_LEVELS)}",
            file=sys.stderr,
        )
        return 2

    try:
        with open(args.path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"文件读取失败：{args.path}（{exc}）", file=sys.stderr)
        return 2

    # 只按 LF 拆分物理行，并去掉 CRLF 的 \r，保留行内其余字符
    lines = [line[:-1] if line.endswith("\r") else line
             for line in content.split("\n")]
    matches, warnings = iter_matches(lines, level, request_id=args.request_id)
    for lineno, message in warnings:
        print(f"第 {lineno} 行：{message}", file=sys.stderr)
    for lineno, raw in matches:
        print(f"{lineno}\t{raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
