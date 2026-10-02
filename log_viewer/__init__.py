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


def iter_matches(lines, level, request_id=None, since=None, until=None):
    """遍历物理行，产出 (行号, 原始行, 警告)。

    lines 为已按行拆分且去掉行末换行符的字符串序列（行号从 1 开始）。
    空白行跳过；无效日志行产出警告；匹配行产出原始内容。
    request_id 为 None 时只按级别筛选；否则还要求顶层 request_id 字段
    是与之严格相等的字符串（区分大小写，不去除首尾空白，不做子串匹配）；
    字段缺失、为 null 或非字符串仅视为不匹配，不产生警告。
    since、until 均为 None 时不检查 timestamp；否则（datetime，UTC）
    只要给出任一时刻就启用时间检查，所有可解析为对象且 level 合法的非空记录
    都检查顶层 timestamp：缺失、为 null、非字符串或格式非法时产出警告并跳过该行。
    since 为含起点的下界（record_time >= since），until 为不含终点的上界
    （record_time < until）；合法但落在区间外的记录静默跳过。
    返回 (matches, warnings)，均为 (行号, 文本) 列表。
    """
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
        if record_level != level:
            continue
        if request_id is not None:
            value = record.get("request_id")
            if not isinstance(value, str) or value != request_id:
                continue
        if since is not None and record_time < since:
            continue
        if until is not None and record_time >= until:
            continue
        matches.append((lineno, raw))
    return matches, warnings
