"""--summary 与 --message-contains 组合使用的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / json / tempfile），
测试输入由各用例在临时目录中以 UTF-8 自行生成，不依赖仓库外样例、
网络或外部服务；产品代码、公开命令参数与模块返回约定保持不变。

覆盖要点（标准输出、标准错误、退出码分别断言，失败信息可定位差异）：
- 摘要只统计同时满足级别、时间与消息子串条件的记录：matched_count 与
  by_level 不含消息不匹配、时间落在区间外或 message 缺失的记录。
- 时间检查产生的无效行警告在摘要模式下保留：invalid_count 与 stderr
  警告不受消息条件影响，按原行序各一条。
- 星号与句点按普通文字匹配：``a.*b`` 不命中 ``axb``。
- 空区间（--until 等于 --since）时命中与各级别计数全部为零，
  无效计数、警告与退出码不变。

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

# ---------------------------------------------------------------------------
# 固定样例的 8 个物理行（不含行分隔符）。
# 除另行说明外，对象 level 为 ERROR、timestamp 为起点、message 为 a.*b。
# 行号在样例中固定，断言显式写出预期行号，不随行序变化隐式推导。
# ---------------------------------------------------------------------------
LINE1 = '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"a.*b"}'
# 第 2 行：level 带首尾空格的小写 warning（规范化后 WARNING），时间为中点。
LINE2 = '{"level":" warning ","timestamp":"2026-10-03T10:00:01Z","message":"a.*b"}'
# 第 3 行：仅 message 改为 axb——"a.*b" 按普通文字匹配时不命中。
LINE3 = '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"axb"}'
# 第 4 行：仅时间改为终点——until 不含终点，静默跳过。
LINE4 = '{"level":"ERROR","timestamp":"2026-10-03T10:00:02Z","message":"a.*b"}'
# 第 5 行：timestamp 为 null——时间检查启用时产生无效行警告。
LINE5 = '{"level":"INFO","timestamp":null,"message":"other"}'
# 第 6 行：删除 message——静默不匹配，不产生警告。
LINE6 = '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z"}'
# 第 7 行：损坏 JSON，产生 JSON 解析失败警告。
LINE7 = "not-json"
# 第 8 行：只含三个空格的空白行，跳过且无警告。
LINE8 = "   "
FIXTURE_LINES = [
    LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7, LINE8,
]
F_PAYLOAD = ("\n".join(FIXTURE_LINES) + "\n").encode("utf-8")

# 时间戳无效与 JSON 损坏各一条警告，按原行序写到 stderr；空白行无警告。
F_STDERR = (
    "第 5 行：无效日志：timestamp 缺失或格式无效\n"
    "第 7 行：无效日志：JSON 解析失败\n"
)

FIVE_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def run_cli(*args):
    """在项目根目录下运行 ``python -m log_viewer``，返回完成的进程。"""
    env = dict(os.environ)
    # 固定子进程的输出编码与 argparse 等标准库消息语言，使断言跨环境稳定；
    # 应用自身的中文消息不受这些变量影响。
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
    """--summary 只统计同时满足消息子串条件的记录，无效行警告保留。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "combined.jsonl"
        path.write_bytes(F_PAYLOAD)
        self.path = str(path)

    def _run(self, until=UNTIL):
        # 重复选择 ERROR、WARNING、ERROR：重复值不改变匹配集合；
        # 消息检索值 a.*b 与起止时间同时给出，再启用 --summary。
        return run_cli(
            self.path,
            "--level", "ERROR", "--level", "WARNING", "--level", "ERROR",
            "--message-contains", "a.*b",
            "--since", SINCE,
            "--until", until,
            "--summary",
        )

    def _assert_summary_only_output(self, proc):
        """stdout 恰为一个 JSON 对象加末尾换行，不夹带原始记录或行号前缀。"""
        self.assertTrue(proc.stdout.endswith("\n"))
        self.assertNotIn("\n\n", proc.stdout)
        self.assertNotIn("\t", proc.stdout)
        for line in FIXTURE_LINES:
            if line.strip():
                self.assertNotIn(line, proc.stdout)
        return json.loads(proc.stdout)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：8 个物理行，第 7 行损坏、第 8 行空白，行号约定成立。
        self.assertEqual(len(FIXTURE_LINES), 8)
        self.assertEqual(F_PAYLOAD.count(b"\n"), 8)
        self.assertEqual(LINE7, "not-json")
        self.assertEqual(LINE8, "   ")

    def test_summary_counts_only_message_matching_records(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = self._assert_summary_only_output(proc)
        # 仅第 1、2 行命中：第 3 行消息不匹配（星号、句点按普通文字），
        # 第 4 行时间恰在不含的终点，第 6 行 message 缺失静默跳过。
        self.assertEqual(obj["matched_count"], 2)
        # 无效计数只来自时间检查与 JSON 解析，与消息条件无关。
        self.assertEqual(obj["invalid_count"], 2)
        self.assertEqual(
            obj["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 1, "ERROR": 1, "CRITICAL": 0},
        )
        # 警告按原行序恰有两条：第 5 行时间戳无效、第 7 行 JSON 解析失败。
        self.assertEqual(proc.stderr, F_STDERR)

    def test_empty_interval_zeroes_matches_but_keeps_warnings(self):
        # 只将结束时间改为起点：合法空区间，命中与各级别计数全部为零，
        # 无效计数、两条警告与退出码保持不变。
        proc = self._run(until=SINCE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = self._assert_summary_only_output(proc)
        self.assertEqual(obj["matched_count"], 0)
        self.assertEqual(obj["invalid_count"], 2)
        self.assertEqual(obj["by_level"], {level: 0 for level in FIVE_LEVELS})
        self.assertEqual(proc.stderr, F_STDERR)


if __name__ == "__main__":
    unittest.main()
