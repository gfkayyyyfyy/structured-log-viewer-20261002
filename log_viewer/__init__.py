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


def normalize_level(value):
    """规范化级别字符串：去除首尾空白并转大写。非字符串返回 None。"""
    if not isinstance(value, str):
        return None
    return value.strip().upper()


def iter_matches(lines, level, request_id=None, since=None, until=None,
                 message_contains=None):
    """遍历物理行，产出 (行号, 原始行, 警告)。

    lines 为已按行拆分且去掉行末换行符的字符串序列（行号从 1 开始）。
    空白行跳过；无效日志行产出警告；匹配行产出原始内容。
    level 为字符串时按单个级别相等匹配（与旧版调用约定完全兼容）；
    也可传入级别字符串序列（元素应为已规范化、属于 SUPPORTED_LEVELS 的值），
    此时记录级别属于其中任一所选级别即通过：只做相等匹配，不解释为严重程度
    阈值；重复级别不增加输出，序列顺序也不影响匹配行顺序（始终按文件行序）。
    request_id 为 None 时只按级别筛选；为字符串时按单个标识精确相等匹配
    （与旧版调用约定完全兼容）；也可传入标识字符串序列，此时顶层
    request_id 字段等于其中任一所选标识即通过。比较区分大小写、不去除
    首尾空白、不做子串匹配；重复标识不增加输出，序列顺序也不影响匹配行
    顺序（始终按文件行序）；字段缺失、为 null 或非字符串仅视为不匹配，
    不产生警告。
    since、until 均为 None 时不检查 timestamp；否则（datetime，UTC）
    只要给出任一时刻就启用时间检查，所有可解析为对象且 level 合法的非空记录
    都检查顶层 timestamp（包括级别不在所选集合内的记录）：缺失、为 null、
    非字符串或格式非法时产出警告并跳过该行。
    since 为含起点的下界（record_time >= since），until 为不含终点的上界
    （record_time < until）；合法但落在区间外的记录静默跳过。
    message_contains 为 None 时不检查 message；否则还要求顶层 message 字段
    是包含该子串的字符串（区分大小写，双方都不去除首尾空白，星号、句点等
    按普通文字做子串匹配，Unicode 按解码后的文字比较）；字段缺失、为 null
    或非字符串仅视为不匹配，不产生警告。
    返回 (matches, warnings)，均为 (行号, 文本) 列表。
    """
    # 字符串按单级别处理；其余按级别序列处理，记录命中其中任一即通过。
    selected_levels = (level,) if isinstance(level, str) else tuple(level)
    # request_id 同理：字符串按单标识处理，序列按标识集合处理。
    if request_id is None or isinstance(request_id, str):
        selected_request_ids = request_id
    else:
        selected_request_ids = tuple(request_id)
    check_time = since is not None or until is not None
    matches = []
    warnings = []
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
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
            if isinstance(selected_request_ids, str):
                if not isinstance(value, str) or value != selected_request_ids:
                    continue
            elif not isinstance(value, str) or value not in selected_request_ids:
                continue
        if message_contains is not None:
            value = record.get("message")
            if not isinstance(value, str) or message_contains not in value:
                continue
        if since is not None and record_time < since:
            continue
        if until is not None and record_time >= until:
            continue
        matches.append((lineno, raw))
    return matches, warnings
