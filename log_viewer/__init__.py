"""本地 JSONL 日志查看器：按级别筛选并输出匹配的原始行。"""

import json
import re
from datetime import datetime

SUPPORTED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 严格的 UTC 时间戳：YYYY-MM-DDTHH:MM:SSZ。
# 不接受小数秒、时区偏移、小写分隔字母或首尾空白。
# 末尾用 \Z 而非 $：Python 的 $ 还会匹配字符串末尾单个换行符之前的位置，
# 会放过结尾带真实 LF 的值；\Z 只匹配字符串真正的末尾。
TIMESTAMP_PATTERN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z\Z"
)


def parse_timestamp(value):
    """解析严格的 UTC 时间戳 YYYY-MM-DDTHH:MM:SSZ，返回 datetime；非法时返回 None。

    年份为四位且非零，其余数字部分为两位，日期与时分秒须有效（不含闰秒）。
    """
    if not isinstance(value, str):
        return None
    match = TIMESTAMP_PATTERN.match(value)
    if not match:
        return None
    try:
        return datetime(*[int(part) for part in match.groups()])
    except ValueError:
        return None


# --line-range 的 START:END 形式：两端都是至少一位的 ASCII 十进制数字，
# 不允许符号、空白、其他分隔符或多出的冒号；前导零允许（解析时规范化去除）。
# 用 [0-9] 而非 \d：后者还会匹配全角数字等其他 Unicode 十进制字符。
LINE_RANGE_PATTERN = re.compile(r"^([0-9]+):([0-9]+)\Z")


def parse_line_range(value):
    """解析 --line-range 的 START:END 区间文本。

    两端都必须是只含 ASCII 十进制数字的非空串（允许前导零，不接受符号、
    空白或多余冒号）。合法时返回 (start, end)，为去掉前导零后的十进制
    数字符串（全零规范化为 "0"）；不做 int 转换，因此位数不受
    sys.get_int_max_str_digits 限制。格式非法时返回 None；
    端点是否为零、起点是否大于终点由调用方结合 compare_decimal_strings
    判定。
    """
    if not isinstance(value, str):
        return None
    match = LINE_RANGE_PATTERN.match(value)
    if not match:
        return None
    start, end = (digits.lstrip("0") or "0" for digits in match.groups())
    return start, end


def compare_decimal_strings(left, right):
    """比较两个无前导零的十进制数字符串，返回 -1、0 或 1。

    先比位数再按字典序比较，等价于数值比较，但不经过 int 转换，
    因此不受 sys.get_int_max_str_digits 的位数限制。
    """
    if len(left) != len(right):
        return -1 if len(left) < len(right) else 1
    if left == right:
        return 0
    return -1 if left < right else 1


def normalize_level(value):
    """规范化级别字符串：去除首尾空白并转大写。非字符串返回 None。"""
    if not isinstance(value, str):
        return None
    return value.strip().upper()


def iter_matches(lines, level, request_id=None, since=None, until=None,
                 message_contains=None, message_excludes=None):
    """遍历物理行，产出 (行号, 原始行, 警告)。

    lines 为已按行拆分且去掉行末换行符的字符串序列（行号从 1 开始）。
    空白行跳过；无效日志行产出警告；匹配行产出原始内容。
    level 为字符串时按单个级别相等匹配（与旧版调用约定完全兼容）；
    也可传入级别字符串序列（元素应为已规范化、属于 SUPPORTED_LEVELS 的值），
    此时记录级别属于其中任一所选级别即通过：只做相等匹配，不解释为严重程度
    阈值；重复级别不增加输出，序列顺序也不影响匹配行顺序（始终按文件行序）。
    request_id 为 None 时只按级别筛选；为字符串时按单个标识处理（与旧版
    调用约定完全兼容）；也可传入标识字符串序列，此时记录顶层 request_id
    字段等于其中任一值即通过：只做严格相等匹配（区分大小写，不去除首尾
    空白，不做子串匹配），重复标识不增加输出，序列顺序也不影响匹配行顺序
    （始终按文件行序）；字段缺失、为 null、非字符串或只在嵌套对象中出现
    仅视为不匹配，不产生警告。
    since、until 均为 None 时不检查 timestamp；否则（datetime，UTC）
    只要给出任一时刻就启用时间检查，所有可解析为对象且 level 合法的非空记录
    都检查顶层 timestamp（包括级别不在所选集合内的记录）：缺失、为 null、
    非字符串或格式非法时产出警告并跳过该行。
    since 为含起点的下界（record_time >= since），until 为不含终点的上界
    （record_time < until）；合法但落在区间外的记录静默跳过。
    message_contains 为 None 时不检查 message；为字符串时按单个子串处理
    （与旧版调用约定完全兼容）；也可传入子串字符串序列，此时还要求顶层
    message 字段是包含其中任一子串的字符串（多个候选取并集，再与级别、
    请求标识、时间条件取交集）。每个候选都是区分大小写的连续子串匹配，
    双方都不去除首尾空白，星号、句点等按普通文字处理，Unicode 按 JSON
    解码后的文字比较；候选重复、序列顺序或一条消息同时命中多个候选都不
    影响结果：始终按文件行序，每条记录至多进入 matches 一次。字段缺失、
    为 null 或非字符串（或只在嵌套对象中出现）仅视为不匹配，不产生警告。
    message_excludes 为 None 时不做排除；为字符串时按单个排除值处理，
    也可传入排除值字符串序列，此时顶层 message 字段是字符串且包含其中
    任一排除值的记录被剔除（与 message_contains 同时给出时，记录既要满足
    包含条件，也不能命中任何排除值）。每个排除值都是区分大小写的连续子串
    匹配，双方都不去除首尾空白，星号、句点等按普通文字处理；排除值重复、
    序列顺序或一条消息同时命中多个排除值都不影响结果。字段缺失、为 null、
    非字符串或只在嵌套对象中出现时不因排除条件丢弃，也不产生警告。
    返回 (matches, warnings)，均为 (行号, 文本) 列表。
    """
    # 字符串按单级别处理；其余按级别序列处理，记录命中其中任一即通过。
    selected_levels = (level,) if isinstance(level, str) else tuple(level)
    # request_id 同理：字符串按单个标识处理，序列按标识集合处理，
    # 记录顶层 request_id 等于其中任一值即通过。
    if request_id is None:
        selected_request_ids = None
    elif isinstance(request_id, str):
        selected_request_ids = (request_id,)
    else:
        selected_request_ids = frozenset(request_id)
    check_time = since is not None or until is not None
    # message_contains 同理：字符串按单个子串处理，序列按候选列表处理，
    # 顶层 message 包含其中任一候选即通过。any 短路判定，候选重复不改变
    # 匹配集合；匹配仍按文件行序逐条判定，一条记录命中多个候选也只输出一次。
    if message_contains is None:
        message_needles = None
    elif isinstance(message_contains, str):
        message_needles = (message_contains,)
    else:
        message_needles = tuple(message_contains)
    # message_excludes 同理：字符串按单个排除值处理，序列按排除值集合处理，
    # 顶层 message 为字符串且包含任一排除值即剔除。排除值重复不改变结果。
    if message_excludes is None:
        message_excluded_needles = None
    elif isinstance(message_excludes, str):
        message_excluded_needles = (message_excludes,)
    else:
        message_excluded_needles = tuple(message_excludes)
    matches = []
    warnings = []
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except (ValueError, RecursionError):
            # JSONDecodeError 是 ValueError 的子类，此处一并覆盖；
            # 此外 Python 3.11+ 在整数转换位数受限（sys.get_int_max_str_digits，
            # 可用 PYTHONINTMAXSTRDIGITS 调整）时，对超过限制的 JSON 数字
            # 直接抛出普通 ValueError 而非 JSONDecodeError，同样按解析失败
            # 处理：只跳过该物理行并产出一条警告，不中断整个文件的处理。
            # 嵌套过深时 JSON 解码器抛出 RecursionError，也按解析失败处理：
            # 异常传播到此处时调用栈已完全展开，后续行的解码不受影响；
            # 不新增深度阈值，也不调整解释器递归上限。
            warnings.append((lineno, "无效日志：JSON 解析失败"))
            continue
        if not isinstance(record, dict):
            warnings.append((lineno, "无效日志：顶层不是 JSON 对象"))
            continue
        record_level = normalize_level(record.get("level"))
        if record_level not in SUPPORTED_LEVELS:
            warnings.append((lineno, "无效日志：level 缺失或不属于支持的级别"))
            continue
        record_time = None
        if check_time:
            record_time = parse_timestamp(record.get("timestamp"))
            if record_time is None:
                warnings.append((lineno, "无效日志：timestamp 缺失或格式无效"))
                continue
        if record_level not in selected_levels:
            continue
        if selected_request_ids is not None:
            value = record.get("request_id")
            if not isinstance(value, str) or value not in selected_request_ids:
                continue
        if message_needles is not None:
            value = record.get("message")
            # 顶层 message 必须是字符串且包含任一候选；只取一次 record.get，
            # 嵌套对象中的 message 不会被触及。
            if not isinstance(value, str) or not any(
                needle in value for needle in message_needles
            ):
                continue
        if message_excluded_needles is not None:
            value = record.get("message")
            # 排除检查在 timestamp 校验之后：启用时间筛选时非法 timestamp
            # 仍照常警告，不被排除条件遮蔽。message 缺失、为 null 或非字符串
            # 时不因排除条件丢弃；只取顶层字段，嵌套对象中的 message 不参与。
            if isinstance(value, str) and any(
                needle in value for needle in message_excluded_needles
            ):
                continue
        if since is not None and record_time < since:
            continue
        if until is not None and record_time >= until:
            continue
        matches.append((lineno, raw))
    return matches, warnings
