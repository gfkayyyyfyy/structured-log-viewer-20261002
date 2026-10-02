"""本地 JSONL 日志查看器：按级别筛选并输出匹配的原始行。"""

import json

SUPPORTED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def normalize_level(value):
    """规范化级别字符串：去除首尾空白并转大写。非字符串返回 None。"""
    if not isinstance(value, str):
        return None
    return value.strip().upper()


def iter_matches(lines, level, request_id=None):
    """遍历物理行，产出 (行号, 原始行, 警告)。

    lines 为已按行拆分且去掉行末换行符的字符串序列（行号从 1 开始）。
    空白行跳过；无效日志行产出警告；匹配行产出原始内容。
    request_id 为 None 时只按级别筛选；否则还要求顶层 request_id 字段
    是与之严格相等的字符串（区分大小写，不去除首尾空白，不做子串匹配）；
    字段缺失、为 null 或非字符串仅视为不匹配，不产生警告。
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
        if record_level != level:
            continue
        if request_id is not None:
            value = record.get("request_id")
            if not isinstance(value, str) or value != request_id:
                continue
        matches.append((lineno, raw))
    return matches, warnings
