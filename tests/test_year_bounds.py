"""年份上下界（0001 年与 9999 年）的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中生成并在用例结束时清理，
不依赖 sample.jsonl、cr.jsonl 或外部服务。

覆盖四组行为：
1. 0001 年与 9999 年是合法的四位非零年份：边界时刻的记录正常参与
   含起点、不含终点的区间筛选，输出行保留原始行号、制表符与原文；
2. 0000 年（年份为零）与五位年份（10000 年）的记录是非法 timestamp，
   启用时间筛选时各产生一条警告，按行序排列；
3. parse_timestamp 对边界合法值返回年月日与时分秒一致的 datetime，
   对 0000 年与五位年份返回 None；
4. 0000 年与五位年份作为 --since / --until 参数时在读取文件前被拒绝
   （退出码 2，标准输出为空，标准错误含参数错误与相应选项）。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from log_viewer import parse_timestamp

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 年份上下界的六个物理行：每行都是仅含顶层 level 和 timestamp 的对象，
# level 均为 ERROR。第 1、2 行在下界 0001 年，第 3、4 行在上界 9999 年，
# 第 5 行年份为零（非法），第 6 行年份为五位（非法）。
BOUND_LINES = [
    '{"level":"ERROR","timestamp":"0001-01-01T00:00:00Z"}',
    '{"level":"ERROR","timestamp":"0001-01-01T00:00:01Z"}',
    '{"level":"ERROR","timestamp":"9999-12-31T23:59:58Z"}',
    '{"level":"ERROR","timestamp":"9999-12-31T23:59:59Z"}',
    '{"level":"ERROR","timestamp":"0000-01-01T00:00:00Z"}',
    '{"level":"ERROR","timestamp":"10000-01-01T00:00:00Z"}',
]
# 第 1、3、4 行的合法边界时刻，用作 --since / --until 参数。
LOWER_START = "0001-01-01T00:00:00Z"
LOWER_END = "0001-01-01T00:00:01Z"
UPPER_START = "9999-12-31T23:59:58Z"
UPPER_END = "9999-12-31T23:59:59Z"
# 第 5、6 行的非法年份值，也用作 --since / --until 的非法参数。
YEAR_ZERO = "0000-01-01T00:00:00Z"
FIVE_DIGIT_YEAR = "10000-01-01T00:00:00Z"
# 启用时间筛选时第 5、6 行各产生一条警告，按行序排列。
EXPECTED_WARNINGS = (
    "第 5 行：无效日志：timestamp 缺失或格式无效\n"
    "第 6 行：无效日志：timestamp 缺失或格式无效\n"
)


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


class YearBoundFilterTests(unittest.TestCase):
    """0001/9999 年边界区间的命令行行为：匹配行、警告、退出码分别断言。"""

    def _write_bound_log(self, tmpdir) -> str:
        # 六行 UTF-8、LF 分隔且末行带换行的 JSONL。
        path = Path(tmpdir) / "year_bounds.jsonl"
        path.write_text("\n".join(BOUND_LINES) + "\n", encoding="utf-8")
        return str(path)

    def test_lower_bound_half_open_interval(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                self._write_bound_log(d), "--level", "ERROR",
                "--since", LOWER_START, "--until", LOWER_END,
            )
        # 退出码、匹配行、警告分别断言，失败时能区分三者差异。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 1 行：第 2 行恰在终点（不含终点，静默跳过）。
        self.assertEqual(proc.stdout, "1\t" + BOUND_LINES[0] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_WARNINGS)

    def test_upper_bound_half_open_interval(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                self._write_bound_log(d), "--level", "ERROR",
                "--since", UPPER_START, "--until", UPPER_END,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 3 行：第 4 行恰在终点（不含终点，静默跳过）。
        self.assertEqual(proc.stdout, "3\t" + BOUND_LINES[2] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_WARNINGS)

    def test_since_only_at_upper_bound(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                self._write_bound_log(d), "--level", "ERROR",
                "--since", UPPER_END,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 4 行：含起点，第 1、2、3 行都早于起点（静默跳过）。
        self.assertEqual(proc.stdout, "4\t" + BOUND_LINES[3] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_WARNINGS)

    def test_without_time_options_all_lines_output(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(self._write_bound_log(d), "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 不传时间选项时不检查 timestamp：六行全部输出，包括年份非法的
        # 第 5、6 行，标准错误为空。
        self.assertEqual(
            proc.stdout,
            "".join(
                f"{lineno}\t{raw}\n"
                for lineno, raw in enumerate(BOUND_LINES, start=1)
            ),
        )
        self.assertEqual(proc.stderr, "")


class YearBoundParseTests(unittest.TestCase):
    """parse_timestamp 对年份上下界值的直接行为。"""

    def test_boundary_years_parse_to_matching_datetime(self):
        cases = [
            (LOWER_START, datetime(1, 1, 1, 0, 0, 0)),
            (UPPER_END, datetime(9999, 12, 31, 23, 59, 59)),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(parse_timestamp(value), expected)

    def test_invalid_years_return_none(self):
        for value in (YEAR_ZERO, FIVE_DIGIT_YEAR):
            with self.subTest(value=value):
                self.assertIsNone(parse_timestamp(value))


class YearBoundArgumentTests(unittest.TestCase):
    """非法年份作为 --since / --until 参数时在读取文件前被拒绝。"""

    def test_invalid_years_rejected_as_arguments_before_reading_file(self):
        cases = [
            ("--since", YEAR_ZERO),
            ("--until", YEAR_ZERO),
            ("--since", FIVE_DIGIT_YEAR),
            ("--until", FIVE_DIGIT_YEAR),
        ]
        with tempfile.TemporaryDirectory() as d:
            # 路径在临时目录中确定不存在，用于证明参数校验先于文件读取。
            missing = str(Path(d) / "no-such-file.jsonl")
            for option, value in cases:
                with self.subTest(f"{option} {value}"):
                    proc = run_cli(
                        missing, "--level", "ERROR", option, value
                    )
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn(option, proc.stderr)
                    # 不读取文件：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)


if __name__ == "__main__":
    unittest.main()
