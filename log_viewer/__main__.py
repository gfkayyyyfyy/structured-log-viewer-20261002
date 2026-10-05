"""命令行入口：python -m log_viewer <文件路径> --level <级别> [--level <级别> ...] [--request-id <标识> ...] [--message-contains <子串> ...] [--message-excludes <子串> ...] [--line-range START:END]"""

import argparse
import json
import sys

from . import (
    SUPPORTED_LEVELS,
    compare_decimal_strings,
    iter_matches,
    normalize_level,
    parse_line_range,
    parse_timestamp,
)


def _fail(message):
    """向标准错误写一条参数错误（保留末尾换行）并返回 None。

    所有校验失败都经由此函数：标准输出保持为空，由调用方据此返回退出码 2，
    且失败发生在读取文件之前。
    """
    print(message, file=sys.stderr)
    return None


def _validate_options(args):
    """校验参数解析器已接受的输入，全部在读取文件之前完成。

    多个问题同时存在时只报告下面固定顺序中的第一个；选项在命令行中的排列
    不影响该顺序。失败返回 None（错误已写入标准错误）；成功返回包含校验后
    取值的字典：levels（已规范化）、request_ids、message_contains、
    message_excludes、since、until、line_range（均可能为 None）。

      1. --jsonl 与 --summary 互斥
      2. 每个 --level 的级别合法性（逐个校验）
      3. 每个 --request-id 非空白（逐个校验，合法值保留首尾空白）
      4. 每个 --message-contains 非空白（逐个校验，合法值保留首尾空白）
      5. 每个 --message-excludes 非空白（逐个校验，合法值保留首尾空白）
      6. --since 的 UTC 格式
      7. --until 的 UTC 格式
      8. --since 不晚于 --until（起止相等合法）
      9. --line-range：重复提供 → 格式 → 零端点 → 起点大于终点
    """
    # 1. 两种输出模式互斥：在读取文件之前拒绝，标准输出保持为空。
    if args.jsonl and args.summary:
        return _fail("参数错误：--jsonl 与 --summary 不能同时使用")

    # 2. --level 可重复提供：每个值各自规范化（忽略大小写及首尾空白），
    # 任一值为空、全空白或不受支持都判为参数错误，后续合法值不能覆盖。
    levels = []
    for raw_level in args.level:
        level = normalize_level(raw_level)
        if level not in SUPPORTED_LEVELS:
            return _fail(
                "参数错误：--level 必须是以下级别之一："
                + ", ".join(SUPPORTED_LEVELS)
            )
        levels.append(level)

    # 3. 未传 --request-id 时为 None；可重复提供，每个值各自校验，任一值
    # 为空字符串或全空白都判为参数错误，后续合法值不能覆盖。合法值原样
    # 保留：不去除首尾空白。
    request_ids = args.request_id
    if request_ids is not None:
        for request_id in request_ids:
            if not request_id.strip():
                return _fail("参数错误：--request-id 不能为空或全为空白")

    # 4. 未传 --message-contains 时为 None；可重复提供，每个值各自校验，
    # 任一值为空字符串或全空白都判为参数错误，即使其他值合法也不能覆盖。
    # 合法值原样保留：不去除首尾空白，星号、句点按普通文字处理。
    message_contains = args.message_contains
    if message_contains is not None:
        for needle in message_contains:
            if not needle.strip():
                return _fail(
                    "参数错误：--message-contains 不能为空或全为空白"
                )

    # 5. 未传 --message-excludes 时为 None；可重复提供，每个值各自校验，
    # 任一值为空字符串或全空白都判为参数错误，即使其他值合法也不能覆盖。
    # 合法值原样保留：不去除首尾空白，星号、句点按普通文字处理。
    message_excludes = args.message_excludes
    if message_excludes is not None:
        for needle in message_excludes:
            if not needle.strip():
                return _fail(
                    "参数错误：--message-excludes 不能为空或全为空白"
                )

    # 6/7. 未传时为 None；传了但为空或格式非法视为参数错误，不读取文件。
    since = None
    if args.since is not None:
        since = parse_timestamp(args.since)
        if since is None:
            return _fail(
                "参数错误：--since 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间"
            )
    until = None
    if args.until is not None:
        until = parse_timestamp(args.until)
        if until is None:
            return _fail(
                "参数错误：--until 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间"
            )

    # 8. 起点严格晚于终点为参数错误；起止相等是合法的空区间。
    if since is not None and until is not None and since > until:
        return _fail("参数错误：--since 不能晚于 --until")

    # 9. 未传 --line-range 时为 None。先拒绝重复提供；区间文本必须是
    # START:END：两端为只含 ASCII 数字的正十进制整数（允许前导零，不接受
    # 符号或空白）；端点为零、起点大于终点同样在读取文件之前拒绝。
    # 端点以去前导零的数字符串保存，不转 int，位数不受
    # sys.get_int_max_str_digits 限制。
    line_range = None
    if args.line_range is not None:
        if len(args.line_range) > 1:
            return _fail("参数错误：--line-range 只能提供一次")
        line_range = parse_line_range(args.line_range[0])
        if line_range is None:
            return _fail(
                "参数错误：--line-range 必须是 START:END 形式，"
                "两端为不含符号和空白的正十进制整数"
            )
        if "0" in line_range:
            return _fail("参数错误：--line-range 的端点不能为零")
        if compare_decimal_strings(line_range[0], line_range[1]) > 0:
            return _fail("参数错误：--line-range 的起点不能大于终点")

    return {
        "levels": levels,
        "request_ids": request_ids,
        "message_contains": message_contains,
        "message_excludes": message_excludes,
        "since": since,
        "until": until,
        "line_range": line_range,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m log_viewer",
        description="读取 JSONL 日志文件，按级别筛选并输出匹配的原始行。",
    )
    parser.add_argument("path", help="JSONL 日志文件路径（UTF-8 编码）")
    parser.add_argument(
        "--level",
        action="append",
        required=True,
        help="筛选级别：DEBUG、INFO、WARNING、ERROR、CRITICAL（忽略大小写及"
             "首尾空白）；可重复提供以选择多个级别，记录满足任一所选级别"
             "即通过（相等匹配，非严重程度阈值）",
    )
    parser.add_argument(
        "--request-id",
        action="append",
        default=None,
        help="可选：仅输出顶层 request_id 字段与之精确相等（区分大小写、"
             "保留首尾空白、不做子串匹配）的记录；可重复提供以选择多个"
             "请求标识，记录等于其中任一值即通过",
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
             "与 --since 同时给出时为含起点、不含终点的区间",
    )
    parser.add_argument(
        "--message-contains",
        action="append",
        default=None,
        help="可选：仅输出顶层 message 字段包含该子串的记录（区分大小写，"
             "双方都不去除首尾空白，星号、句点等按普通文字匹配）；"
             "可重复提供以给出多个候选，message 包含其中任一即通过，"
             "再与级别、请求标识和时间条件取交集；候选重复或顺序不影响"
             "结果，一条消息命中多个候选也只输出一次；"
             "message 缺失、为 null 或非字符串的记录静默不匹配",
    )
    parser.add_argument(
        "--message-excludes",
        action="append",
        default=None,
        help="可选：剔除顶层 message 字段包含该子串的记录（区分大小写，"
             "双方都不去除首尾空白，星号、句点等按普通文字匹配）；"
             "可重复提供以给出多个排除值，message 包含其中任一即被剔除，"
             "再与级别、请求标识、时间和消息包含条件取交集；排除值重复或"
             "顺序不影响结果；message 缺失、为 null、非字符串或只出现在"
             "嵌套对象中的记录不因排除条件丢弃，也不产生警告",
    )
    parser.add_argument(
        "--line-range",
        action="append",
        default=None,
        metavar="START:END",
        help="可选：只让原始物理行号落在 [START, END] 闭区间内的记录参与"
             "匹配，与级别等其余筛选条件取交集，按原文件顺序输出且不重新"
             "编号；两端均为包含边界，相等时只选一行；端点只接受 ASCII"
             "正十进制整数（允许前导零，不接受符号或空白）；只能提供一次；"
             "范围只限制匹配结果，全文件的无效行诊断不受影响",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="可选：不输出匹配行，只向标准输出写一个 JSON 统计摘要，"
             "包含 matched_count、invalid_count、by_level 三个字段",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="可选：将匹配记录以 JSONL 写到标准输出，每条记录只输出"
             "原始正文和末尾换行，不带行号与制表符前缀，不加外层数组或"
             "统计字段，正文不重新序列化；不能与 --summary 同时使用",
    )
    args = parser.parse_args(argv)

    # 参数解析成功之后、读取文件之前的全部校验：错误优先级固定且与选项
    # 排列无关；失败时标准输出为空、退出码为 2，且不读取文件。
    options = _validate_options(args)
    if options is None:
        return 2
    levels = options["levels"]
    request_ids = options["request_ids"]
    message_contains = options["message_contains"]
    message_excludes = options["message_excludes"]
    since = options["since"]
    until = options["until"]
    line_range = options["line_range"]

    # newline="" 关闭通用换行转换：不紧邻 LF 的单独 CR 必须原样保留，
    # 不能在读取时被翻译成 LF 而拆开物理行。
    try:
        with open(args.path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"文件读取失败：{args.path}（{exc}）", file=sys.stderr)
        return 2

    # 兼容文件开头的一次 UTF-8 BOM（字节 EF BB BF，解码为 U+FEFF）：
    # 仅当整个文件最前面三个字节恰为该标记时把这一次标记排除在日志正文
    # 之外，它不占物理行号；在解码成功之后才移除，因此标记之后若存在
    # 非法 UTF-8 字节仍按文件读取失败处理。只移除最开头的一次：连续的
    # 第二个标记、空白之后或行内 JSON 对象之前的 U+FEFF 都属于正文，
    # 按既有规则处理（json 无法解析时照常产出含原始行号的警告）；
    # JSON 字符串值中的 U+FEFF 同理原样保留并参与筛选。
    if content.startswith("﻿"):
        content = content[1:]

    # 物理行只由 LF（LF 或 CRLF）终止：按 LF 拆分后，仅对被 LF 终止的
    # 分段（除最后一个外）去掉 CRLF 中紧邻 LF 的那个 CR。
    # 最后一个分段没有终止 LF：即使以 CR 结尾也原样保留（末行结尾的单独 CR）。
    parts = content.split("\n")
    lines = [
        part[:-1] if index < len(parts) - 1 and part.endswith("\r") else part
        for index, part in enumerate(parts)
    ]
    matches, warnings = iter_matches(
        lines, levels, request_ids, since, until, message_contains,
        message_excludes=message_excludes,
    )
    # 行号区间只限制匹配结果，不影响全文件诊断：warnings 保持原样，
    # 区间外的损坏 JSON、顶层非对象、无效级别及（启用时间筛选时）
    # 无效 timestamp 照常警告；区间外的匹配行静默不输出、不计数。
    # 行号为原始物理行号，过滤后不重排、不重新编号。
    if line_range is not None:
        start, end = line_range
        matches = [
            (lineno, raw)
            for lineno, raw in matches
            if compare_decimal_strings(start, str(lineno)) <= 0
            and compare_decimal_strings(str(lineno), end) <= 0
        ]
    for lineno, message in warnings:
        print(f"第 {lineno} 行：{message}", file=sys.stderr)
    if args.summary:
        # 摘要模式：标准输出只有一个 JSON 对象和末尾换行，不写匹配行。
        # by_level 按匹配记录规范化后的 level 计数：命中行必为含合法
        # level 字段的 JSON 对象，此处重新解析不会失败。
        by_level = {level: 0 for level in SUPPORTED_LEVELS}
        for _, raw in matches:
            by_level[normalize_level(json.loads(raw).get("level"))] += 1
        summary = {
            "matched_count": len(matches),
            "invalid_count": len(warnings),
            "by_level": by_level,
        }
        print(json.dumps(summary, ensure_ascii=False))
    elif args.jsonl:
        # JSONL 导出模式：每条匹配记录只写原始正文和一个 LF 终止符，
        # 不带行号和制表符前缀，不加外层数组或统计字段。
        # 直接写字节：正文不重新序列化，首尾空白、字段顺序、中文、
        # JSON 转义以及正文中的单独 CR、U+2028/U+2029 均原样保留；
        # 末行即使源文件没有终止 LF 也补一个 LF，使重定向得到的
        # 文件每行（含最后一条）都以换行结束。
        stdout_buffer = sys.stdout.buffer
        for _, raw in matches:
            stdout_buffer.write(raw.encode("utf-8"))
            stdout_buffer.write(b"\n")
        stdout_buffer.flush()
    else:
        for lineno, raw in matches:
            print(f"{lineno}\t{raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
