"""本地 JSONL 日志查看器：按级别筛选并输出匹配的原始行。"""

import json
import re
from datetime import datetime, timezone

SUPPORTED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 严格的 UTC 时间字面量：YYYY-MM-DDTHH:MM:SSZ。
# 只接受四位年份、两位其余数字部分与大写 T/Z；不接受小数秒、时区偏移等。
_TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$")


def normalize_level(value):
    """规范化级别字符串：去除首尾空白并转大写。非字符串返回 None。"""
    if not isinstance(value, str):
        return None
    return value.strip().upper()


def parse_timestamp(value):
    """解析 YYYY-MM-DDTHH:MM:SSZ 形式的 UTC 时间。

    年份为四位且非零，月份、日期、时分秒须为有效日历/时钟值
    （不接受闰秒）。合法时返回带 UTC 时区的 ``datetime``，否则返回 None。
    非字符串（含 None）同样返回 None。
    """
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP_RE.match(value)
    if match is None:
        return None
    year, month, day, hour, minute, second = (
        int(part) for part in match.groups()
    )
    if year == 0:
        return None
    if not 1 <= month <= 12:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        return None
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2:
        is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        max_day = 29 if is_leap else 28
    else:
        max_day = days_in_month[month - 1]
    if not 1 <= day <= max_day:
        return None
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def iter_matches(lines, level, request_id=None, since=None):
    """遍历物理行，产出 (行号, 原始行, 警告)。

    lines 为已按行拆分且去掉行末换行符的字符串序列（行号从 1 开始）。
    空白行跳过；无效日志行产出警告；匹配行产出原始内容。
    request_id 为 None 时只按级别筛选；否则还要求顶层 request_id 字段
    是与之严格相等的字符串（区分大小写，不去除首尾空白，不做子串匹配）；
    字段缺失、为 null 或非字符串仅视为不匹配，不产生警告。
    since 为 None 时不检查 timestamp；否则须先通过 parse_timestamp
    解析，且时间不早于 since（包含起点）才参与后续筛选。只要记录可解析为
    对象且 level 合法，即使级别或请求标识不匹配也会检查 timestamp：
    timestamp 缺失、为 null、非字符串或格式非法时跳过该行并产生一条
    “无效日志：timestamp 缺失或格式无效”警告。
    返回 (matches, warnings)，均为 (行号, 文本) 列表。
    """
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
        if since is not None:
            timestamp = parse_timestamp(record.get("timestamp"))
            if timestamp is None:
                warnings.append(
                    (lineno, "无效日志：timestamp 缺失或格式无效")
                )
                continue
            if timestamp < since:
                continue
        if record_level != level:
            continue
        if request_id is not None:
            value = record.get("request_id")
            if not isinstance(value, str) or value != request_id:
                continue
        matches.append((lineno, raw))
    return matches, warnings
