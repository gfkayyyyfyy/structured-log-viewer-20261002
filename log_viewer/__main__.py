"""命令行入口：python -m log_viewer <文件路径> --level <级别>"""

import argparse
import sys

from . import SUPPORTED_LEVELS, iter_matches, normalize_level, parse_timestamp


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
        help="可选：仅输出顶层 request_id 字段与之精确相等（区分大小写、"
             "保留首尾空白、不做子串匹配）的记录",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="可选：仅输出顶层 timestamp 不早于该时刻的记录，"
             "格式为 YYYY-MM-DDTHH:MM:SSZ（UTC，含起点）",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="可选：仅输出顶层 timestamp 严格早于该时刻的记录，"
             "格式为 YYYY-MM-DDTHH:MM:SSZ（UTC，不含终点）；"
             "与 --since 组合时为含起点、不含终点的区间",
    )
    args = parser.parse_args(argv)

    level = normalize_level(args.level)
    if level not in SUPPORTED_LEVELS:
        print(
            f"参数错误：--level 必须是以下级别之一：{', '.join(SUPPORTED_LEVELS)}",
            file=sys.stderr,
        )
        return 2

    # 未传 --request-id 时为 None；传了但为空字符串或全空白视为参数错误。
    request_id = args.request_id
    if request_id is not None and not request_id.strip():
        print("参数错误：--request-id 不能为空或全为空白", file=sys.stderr)
        return 2

    # 未传 --since 时为 None；传了但为空或格式非法视为参数错误，不读取文件。
    since = None
    if args.since is not None:
        since = parse_timestamp(args.since)
        if since is None:
            print(
                "参数错误：--since 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间",
                file=sys.stderr,
            )
            return 2

    # 未传 --until 时为 None；传了但为空或格式非法视为参数错误，不读取文件。
    until = None
    if args.until is not None:
        until = parse_timestamp(args.until)
        if until is None:
            print(
                "参数错误：--until 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间",
                file=sys.stderr,
            )
            return 2

    # 合法 --since 晚于 --until 是参数错误；相等是合法的空区间，照常检查记录。
    if since is not None and until is not None and since > until:
        print("参数错误：--since 不能晚于 --until", file=sys.stderr)
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
    matches, warnings = iter_matches(lines, level, request_id, since, until)
    for lineno, message in warnings:
        print(f"第 {lineno} 行：{message}", file=sys.stderr)
    for lineno, raw in matches:
        print(f"{lineno}\t{raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
