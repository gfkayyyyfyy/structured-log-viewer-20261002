"""--summary 统计摘要开关的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / json / tempfile），
测试输入均在用例自建的临时目录中生成。

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

# 验收示例的五个物理行：
# 第 1 行级别不匹配，第 2 行空白，第 3 行非法 JSON，
# 第 4 行（规范化后 ERROR、r-1）唯一同时满足全部条件，第 5 行请求标识不匹配。
ACCEPTANCE_LINES = [
    '{"level":"INFO","request_id":"r-1"}',
    "",
    "not-json",
    '{"level":" error ","request_id":"r-1"}',
    '{"level":"ERROR","request_id":"r-2"}',
]
EXPECTED_STDERR = "第 3 行：无效日志：JSON 解析失败\n"
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


class SummaryModeTests(unittest.TestCase):
    """启用 --summary：stdout 仅一个 JSON 对象，警告仍走 stderr。"""

    def _run(self, lines, *extra, trailing_newline=True):
        payload = "\n".join(lines)
        if trailing_newline:
            payload += "\n"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.jsonl"
            path.write_text(payload, encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR", "--request-id", "r-1",
                *extra,
            )
        return proc

    def _assert_full_summary_shape(self, obj, matched, invalid):
        self.assertEqual(
            set(obj), {"matched_count", "invalid_count", "by_level"}
        )
        self.assertEqual(obj["matched_count"], matched)
        self.assertEqual(obj["invalid_count"], invalid)
        by_level = obj["by_level"]
        self.assertEqual(set(by_level), set(FIVE_LEVELS))
        self.assertTrue(
            all(isinstance(v, int) and v >= 0 for v in by_level.values())
        )
        self.assertEqual(sum(by_level.values()), matched)

    def test_acceptance_example_summary(self):
        proc = self._run(ACCEPTANCE_LINES, "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # stdout 恰为一个 JSON 对象加末尾换行。
        self.assertTrue(proc.stdout.endswith("\n"))
        self.assertNotIn("\n\n", proc.stdout)
        obj = json.loads(proc.stdout)
        self._assert_full_summary_shape(obj, matched=1, invalid=1)
        self.assertEqual(obj["by_level"]["ERROR"], 1)
        self.assertEqual(
            [obj["by_level"][k] for k in ("DEBUG", "INFO", "WARNING", "CRITICAL")],
            [0, 0, 0, 0],
        )
        # 警告仍只指向第 3 行，按原行号和顺序写到 stderr。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_without_summary_keeps_line_output(self):
        proc = self._run(ACCEPTANCE_LINES)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "4\t" + ACCEPTANCE_LINES[3] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_empty_file_outputs_full_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "empty.jsonl"
            path.write_bytes(b"")
            proc = run_cli(str(path), "--level", "ERROR", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self._assert_full_summary_shape(obj, matched=0, invalid=0)
        self.assertTrue(proc.stdout.endswith("\n"))
        self.assertEqual(proc.stderr, "")

    def test_blank_lines_count_as_neither_matched_nor_invalid(self):
        proc = self._run(["", "  ", "\t", "\r"], "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self._assert_full_summary_shape(obj, matched=0, invalid=0)
        self.assertEqual(proc.stderr, "")

    def test_no_match_but_invalid_lines(self):
        proc = self._run(
            ["not-json", '{"level":"INFO","request_id":"r-1"}'],
            "--summary",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self._assert_full_summary_shape(obj, matched=0, invalid=1)
        self.assertEqual(
            proc.stderr, "第 1 行：无效日志：JSON 解析失败\n"
        )

    def test_by_level_counts_each_level_once_after_normalization(self):
        # 多条不同级别记录，含首尾空白/小写形式；重复选择级别不重复计数。
        lines = [
            '{"level":" error ","request_id":"r-1"}',
            '{"level":"INFO","request_id":"r-1"}',
            '{"level":"warning","request_id":"r-1"}',
            '{"level":"DEBUG","request_id":"r-1"}',
            '{"level":"CRITICAL","request_id":"r-1"}',
            '{"level":"ERROR","request_id":"r-1"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path),
                "--level", "ERROR", "--level", " error ",
                "--level", "INFO", "--level", "WARNING",
                "--level", "DEBUG", "--level", "CRITICAL",
                "--request-id", "r-1",
                "--summary",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self._assert_full_summary_shape(obj, matched=6, invalid=0)
        self.assertEqual(
            obj["by_level"],
            {"DEBUG": 1, "INFO": 1, "WARNING": 1, "ERROR": 2, "CRITICAL": 1},
        )

    def test_valid_empty_time_interval_still_summarizes(self):
        lines = [
            '{"level":"ERROR","request_id":"r-1","timestamp":"2026-10-03T10:00:00Z"}',
            '{"level":"ERROR","request_id":"r-1"}',
        ]
        proc = self._run(
            lines, "--summary",
            "--since", "2026-10-03T10:00:00Z",
            "--until", "2026-10-03T10:00:00Z",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        # 合法空区间：恰在终点的记录不匹配；缺 timestamp 的记录计入无效。
        self._assert_full_summary_shape(obj, matched=0, invalid=1)
        self.assertEqual(
            proc.stderr, "第 2 行：无效日志：timestamp 缺失或格式无效\n"
        )

    def test_invalid_timestamp_on_nonmatching_record_counts_as_invalid(self):
        # 级别不命中其他条件，但启用时间检查后时间戳无效仍警告并计入。
        lines = [
            '{"level":"INFO","timestamp":null}',
            '{"level":"ERROR","request_id":"r-1","timestamp":"2026-10-03T10:00:00Z"}',
        ]
        proc = self._run(
            lines, "--summary", "--since", "2026-10-03T09:00:00Z"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        self._assert_full_summary_shape(obj, matched=1, invalid=1)
        self.assertEqual(obj["by_level"]["ERROR"], 1)
        self.assertEqual(
            proc.stderr, "第 1 行：无效日志：timestamp 缺失或格式无效\n"
        )

    def test_summary_does_not_emit_matched_lines(self):
        proc = self._run(ACCEPTANCE_LINES, "--summary")
        self.assertNotIn(ACCEPTANCE_LINES[3], proc.stdout)
        self.assertNotIn("\t", proc.stdout)


class SummaryArgumentErrorTests(unittest.TestCase):
    """参数非法或文件读取失败：退出码 2，stdout 为空，不产出摘要。"""

    def test_bad_level_with_summary_has_empty_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            path.write_text('{"level":"ERROR"}\n', encoding="utf-8")
            proc = run_cli(str(path), "--level", "trace", "--summary")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("参数错误", proc.stderr)

    def test_missing_file_with_summary_has_empty_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            proc = run_cli(missing, "--level", "ERROR", "--summary")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("文件读取失败", proc.stderr)

    def test_summary_is_a_flag_and_takes_no_value(self):
        # 给 --summary 传值会被 argparse 判为无法识别的参数：退出码 2。
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            path.write_text('{"level":"ERROR"}\n', encoding="utf-8")
            proc = run_cli(str(path), "--level", "ERROR", "--summary", "x")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
