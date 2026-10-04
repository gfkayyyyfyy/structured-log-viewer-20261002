"""--message-contains 与 --summary 组合使用的离线回归测试。

确认统计摘要只计入同时满足级别、时间区间和消息子串条件的记录，
同时保留时间检查产生的无效行警告；星号、句点按普通文字匹配。

只用 Python 3 标准库（unittest / subprocess / json / tempfile），
测试输入均在用例自建的临时目录中生成，不依赖仓库外样例。

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

SINCE = "2026-10-03T10:00:00Z"
UNTIL = "2026-10-03T10:00:02Z"
MESSAGE_QUERY = "a.*b"

# 固定样例的八个物理行。除另行说明外，对象为
# {"level":"ERROR","timestamp":起点,"message":"a.*b"}。
SAMPLE_LINES = [
    # 第 1 行：默认内容，命中。
    '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"a.*b"}',
    # 第 2 行：级别为带首尾空格的小写 warning，时间为区间中点，命中。
    '{"level":" warning ","timestamp":"2026-10-03T10:00:01Z","message":"a.*b"}',
    # 第 3 行：消息为 axb，不含普通文字子串 a.*b，静默不匹配。
    '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"axb"}',
    # 第 4 行：时间恰为不含的终点，静默不匹配。
    '{"level":"ERROR","timestamp":"2026-10-03T10:00:02Z","message":"a.*b"}',
    # 第 5 行：时间戳为 null，启用时间检查后计入无效。
    '{"level":"INFO","timestamp":null,"message":"other"}',
    # 第 6 行：缺少 message 字段，静默不匹配。
    '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z"}',
    # 第 7 行：非法 JSON，计入无效。
    "not-json",
    # 第 8 行：全空白，既不匹配也不计入无效。
    "   ",
]

# 两条警告按原行序：第 5 行时间戳无效，第 7 行 JSON 解析失败。
EXPECTED_STDERR = (
    "第 5 行：无效日志：timestamp 缺失或格式无效\n"
    "第 7 行：无效日志：JSON 解析失败\n"
)

FIVE_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


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


class SummaryWithMessageContainsTests(unittest.TestCase):
    """消息子串筛选与统计摘要组合：摘要只统计满足消息条件的记录。"""

    def _run(self, until=UNTIL):
        """用固定样例运行组合筛选：重复选择 ERROR、WARNING、ERROR，
        消息检索值 a.*b，时间区间 [SINCE, until)，启用 --summary。"""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.jsonl"
            path.write_text("\n".join(SAMPLE_LINES) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path),
                "--level", "ERROR",
                "--level", "WARNING",
                "--level", "ERROR",
                "--message-contains", MESSAGE_QUERY,
                "--since", SINCE,
                "--until", until,
                "--summary",
            )
        return proc

    def _assert_stdout_is_single_summary_object(self, proc):
        """标准输出恰为一个 JSON 对象加末尾换行，不夹带原始记录或行号前缀。"""
        self.assertTrue(proc.stdout.endswith("\n"), proc.stdout)
        self.assertNotIn("\n\n", proc.stdout)
        self.assertNotIn("\t", proc.stdout)
        for raw in SAMPLE_LINES[:6]:
            self.assertNotIn(raw, proc.stdout)
        obj = json.loads(proc.stdout)
        self.assertEqual(
            set(obj), {"matched_count", "invalid_count", "by_level"}
        )
        return obj

    def test_summary_counts_only_message_matching_records(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = self._assert_stdout_is_single_summary_object(proc)
        # 仅第 1、2 行命中；第 5、7 行计入无效。
        self.assertEqual(obj["matched_count"], 2)
        self.assertEqual(obj["invalid_count"], 2)
        self.assertEqual(set(obj["by_level"]), set(FIVE_LEVELS))
        self.assertEqual(obj["by_level"]["ERROR"], 1)
        self.assertEqual(obj["by_level"]["WARNING"], 1)
        self.assertEqual(
            [obj["by_level"][k] for k in ("DEBUG", "INFO", "CRITICAL")],
            [0, 0, 0],
        )
        # 警告按原行序恰有两条，退出码为 0。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_empty_interval_keeps_warnings_and_zeroes_counts(self):
        # 仅将结束时间改为起点：合法空区间，命中与各级别计数全部为零，
        # 无效计数、两条警告和退出码保持不变。
        proc = self._run(until=SINCE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = self._assert_stdout_is_single_summary_object(proc)
        self.assertEqual(obj["matched_count"], 0)
        self.assertEqual(obj["invalid_count"], 2)
        self.assertEqual(
            obj["by_level"], {level: 0 for level in FIVE_LEVELS}
        )
        self.assertEqual(proc.stderr, EXPECTED_STDERR)


if __name__ == "__main__":
    unittest.main()
