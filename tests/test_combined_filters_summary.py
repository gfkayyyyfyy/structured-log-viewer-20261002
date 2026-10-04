"""多级别 + 多请求标识 + 含起点不含终点时间区间组合筛选的 --summary 回归测试。

只用 Python 3 标准库（unittest / subprocess / json / tempfile），
测试输入均在用例自建的临时目录中以 UTF-8、LF 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令参数
与模块接口均保持不变，仅验证组合筛选下的 --summary 统计摘要这一条路径。

覆盖要点（七行样例，末行带换行，对象字段均在顶层）：
- ``--level ERROR --level WARNING --request-id r-1 --request-id r-2``
  ``--since 2026-10-03T10:00:00Z --until 2026-10-03T10:00:02Z --summary``
  下：退出码 0；标准输出仅一个 JSON 对象和末尾换行，matched_count 与
  invalid_count 均为 2，by_level 中 ERROR、WARNING 各为 1，其余支持级别
  为 0；不输出日志原文或行号。命中的是第 1 行（ERROR、r-2、恰在起点）
  与第 4 行（level 原文 " warning " 规范化后命中、r-1）；第 5 行请求标识
  r-12 不符、第 7 行时间恰在终点（不含终点）均被排除；第 2 行仅含空格，
  按空白行跳过。
- 标准错误依次只有第 3 行“JSON 解析失败”与第 6 行“timestamp 缺失或
  格式无效”两条警告：第 6 行级别（INFO）不在所选集合内，仍因时间筛选
  启用而产生警告。
- 交换两个 --request-id 的顺序、或再重复添加一个 r-1，退出码、标准输出
  与标准错误逐字节不变。
- 其余输入与参数不变、仅把 --until 改为与 --since 相等（合法空区间）时：
  matched_count 与各级别计数全为 0，invalid_count 仍为 2，警告与退出码
  不变。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 七行样例的物理正文（不含行分隔符）。
# ---------------------------------------------------------------------------
# 第 1 行：ERROR、r-2、时间恰为 --since 起点（含起点，命中）。
LINE1 = (
    '{"level":"ERROR","request_id":"r-2",'
    '"timestamp":"2026-10-03T10:00:00Z"}'
)
# 第 2 行：仅含空格，按空白行跳过，不产生警告。
LINE2 = " "
# 第 3 行：不是 JSON，产生解析失败警告。
LINE3 = "not-json"
# 第 4 行：level 原文带首尾空格的小写 warning（规范化后命中）、r-1、
# 时间在区间内，命中。
LINE4 = (
    '{"level":" warning ","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:01Z"}'
)
# 第 5 行：ERROR，但 request_id 为 r-12，精确相等比较不通过。
LINE5 = (
    '{"level":"ERROR","request_id":"r-12",'
    '"timestamp":"2026-10-03T10:00:01Z"}'
)
# 第 6 行：只有 level 与 request_id 两个字段。级别（INFO）虽不在所选
# 集合内，但时间筛选启用后所有 level 合法的记录都检查 timestamp，
# 缺失仍须警告。
LINE6 = '{"level":"INFO","request_id":"other"}'
# 第 7 行：ERROR、r-1，但时间恰为 --until 终点（不含终点，被排除）。
LINE7 = (
    '{"level":"ERROR","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:02Z"}'
)
SEVEN_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7]
PAYLOAD = "\n".join(SEVEN_LINES) + "\n"

# 标准错误：第 3 行 JSON 解析失败警告在前，第 6 行 timestamp 警告在后。
EXPECTED_STDERR = (
    "第 3 行：无效日志：JSON 解析失败\n"
    "第 6 行：无效日志：timestamp 缺失或格式无效\n"
)

# 组合筛选 + --summary 的标准输出对应的 JSON 对象。
EXPECTED_SUMMARY = {
    "matched_count": 2,
    "invalid_count": 2,
    "by_level": {"DEBUG": 0, "INFO": 0, "WARNING": 1, "ERROR": 1, "CRITICAL": 0},
}

# 合法空区间（--until 与 --since 相等）时的摘要：命中与各级别计数全为 0。
EMPTY_INTERVAL_SUMMARY = {
    "matched_count": 0,
    "invalid_count": 2,
    "by_level": {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0},
}

# 组合筛选的公共参数：级别与请求标识各选两个，时间为含起点、不含终点区间。
FILTER_ARGS = (
    "--level", "ERROR",
    "--level", "WARNING",
    "--since", "2026-10-03T10:00:00Z",
    "--until", "2026-10-03T10:00:02Z",
    "--summary",
)


def run_cli(*args):
    """在项目根目录下运行 ``python -m log_viewer``，返回完成的进程。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


class CombinedFiltersSummaryTests(unittest.TestCase):
    """多级别 + 多请求标识 + 时间区间组合筛选下的 --summary 统计摘要。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "case.jsonl"
        # 二进制写入：保证内容确为 UTF-8、行分隔符确为 LF、末行带换行。
        path.write_bytes(PAYLOAD.encode("utf-8"))
        self._path = str(path)

    def _run(self, *request_id_args):
        return run_cli(self._path, *FILTER_ARGS, *request_id_args)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：样例确为七个物理行，末行带换行，无 CR，各行符合约定。
        self.assertEqual(len(SEVEN_LINES), 7)
        self.assertEqual(PAYLOAD.count("\n"), 7)
        self.assertNotIn("\r", PAYLOAD)
        self.assertTrue(PAYLOAD.endswith("\n"))
        self.assertIn('"request_id":"r-2"', LINE1)
        self.assertIn('"timestamp":"2026-10-03T10:00:00Z"', LINE1)
        self.assertTrue(LINE2.strip() == "" and LINE2 != "")
        self.assertEqual(LINE3, "not-json")
        self.assertIn('"level":" warning "', LINE4)
        self.assertIn('"request_id":"r-12"', LINE5)
        self.assertNotIn("timestamp", LINE6)
        self.assertIn('"request_id":"r-1"', LINE7)
        self.assertIn('"timestamp":"2026-10-03T10:00:02Z"', LINE7)

    def test_combined_filters_summary(self):
        proc = self._run("--request-id", "r-1", "--request-id", "r-2")
        # 退出码、标准输出、标准错误分别独立断言，失败时可区分差异来源。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出恰为一个 JSON 对象加末尾换行。
        self.assertTrue(proc.stdout.endswith("\n"))
        self.assertNotIn("\n\n", proc.stdout)
        self.assertEqual(json.loads(proc.stdout), EXPECTED_SUMMARY)
        # 不输出日志原文或行号：无制表符前缀，各行正文均不出现。
        self.assertNotIn("\t", proc.stdout)
        for line in (LINE1, LINE4, LINE5, LINE7):
            self.assertNotIn(line, proc.stdout)
        # 标准错误依次只有第 3 行与第 6 行的两条既有文案警告。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_request_id_order_and_duplicates_do_not_change_result(self):
        baseline = self._run("--request-id", "r-1", "--request-id", "r-2")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        # 交换两个请求标识的顺序，所有结果逐字节不变。
        swapped = self._run("--request-id", "r-2", "--request-id", "r-1")
        self.assertEqual(swapped.returncode, baseline.returncode)
        self.assertEqual(swapped.stdout, baseline.stdout)
        self.assertEqual(swapped.stderr, baseline.stderr)
        # 再重复添加一个 r-1，所有结果逐字节不变。
        duplicated = self._run(
            "--request-id", "r-1", "--request-id", "r-2", "--request-id", "r-1"
        )
        self.assertEqual(duplicated.returncode, baseline.returncode)
        self.assertEqual(duplicated.stdout, baseline.stdout)
        self.assertEqual(duplicated.stderr, baseline.stderr)

    def test_empty_interval_summarizes_zero_but_keeps_warnings(self):
        # 仅把 --until 改为与 --since 相等：合法空区间，其余输入与参数不变。
        args = [
            arg if arg != "2026-10-03T10:00:02Z" else "2026-10-03T10:00:00Z"
            for arg in FILTER_ARGS
        ]
        proc = run_cli(
            self._path, *args, "--request-id", "r-1", "--request-id", "r-2"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 命中与各级别计数全为 0，invalid_count 仍为 2。
        self.assertEqual(json.loads(proc.stdout), EMPTY_INTERVAL_SUMMARY)
        # 警告与退出码不变（空区间仍检查无效记录）。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)


if __name__ == "__main__":
    unittest.main()
