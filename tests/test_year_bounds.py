"""年份上下界（0001 年与 9999 年）的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中生成并在用例结束时清理，
不依赖 sample.jsonl、cr.jsonl 或外部服务。

覆盖四组行为：
1. 0001-01-01 与 9999-12-31 是合法的四位非零年份边界：作为记录
   timestamp 时正常参与含起点、不含终点的区间筛选，匹配行保留
   原始行号、制表符与原文；
2. 0000 年（年份为零）与 10000 年（五位年份）在记录中是非法
   timestamp：启用时间筛选时各产生一条警告，按行序输出；
3. parse_timestamp 对边界时刻返回年月日、时分秒一致的 datetime，
   对 0000 年与五位年份返回 None；
4. 0000 年与五位年份作为 --since / --until 参数时在读取文件前
   被拒绝（退出码 2，标准输出为空，不报文件读取失败）。

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

# 年份边界的六个物理行，均为仅含顶层 level 与 timestamp 的对象：
# 第 1、2 行是 0001 年（最小合法年份）开头两秒，
# 第 3、4 行是 9999 年（最大合法年份）末尾两秒，
# 第 5 行年份为 0000（年份必须非零，非法），
# 第 6 行年份为 10000（年份必须为四位，非法）。
YEAR_BOUND_LINES = [
    '{"level":"ERROR","timestamp":"0001-01-01T00:00:00Z"}',
    '{"level":"ERROR","timestamp":"0001-01-01T00:00:01Z"}',
    '{"level":"ERROR","timestamp":"9999-12-31T23:59:58Z"}',
    '{"level":"ERROR","timestamp":"9999-12-31T23:59:59Z"}',
    '{"level":"ERROR","timestamp":"0000-01-01T00:00:00Z"}',
    '{"level":"ERROR","timestamp":"10000-01-01T00:00:00Z"}',
]
# 第 5、6 行的非法年份值，也用作 --since / --until 的非法参数。
YEAR_ZERO = "0000-01-01T00:00:00Z"
YEAR_FIVE_DIGITS = "10000-01-01T00:00:00Z"

# 启用时间筛选时第 5、6 行各产生一条警告，按行号顺序。
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


def write_year_bound_log(tmpdir) -> str:
    """把六行测试日志写入临时目录：UTF-8、LF 分隔、末行带换行。"""
    path = Path(tmpdir) / "year_bounds.jsonl"
    path.write_text("\n".join(YEAR_BOUND_LINES) + "\n", encoding="utf-8")
    return str(path)


class MinYearIntervalTests(unittest.TestCase):
    """0001 年区间的命令行行为：匹配行、警告、退出码分别断言。"""

    def test_min_year_half_open_interval(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                write_year_bound_log(d), "--level", "ERROR",
                "--since", "0001-01-01T00:00:00Z",
                "--until", "0001-01-01T00:00:01Z",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 含起点、不含终点：只输出第 1 行，保留原始行号、制表符与原文。
        self.assertEqual(proc.stdout, "1\t" + YEAR_BOUND_LINES[0] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_WARNINGS)


class MaxYearIntervalTests(unittest.TestCase):
    """9999 年区间的命令行行为：匹配行、警告、退出码分别断言。"""

    def test_max_year_half_open_interval(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                write_year_bound_log(d), "--level", "ERROR",
                "--since", "9999-12-31T23:59:58Z",
                "--until", "9999-12-31T23:59:59Z",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 含起点、不含终点：只输出第 3 行。
        self.assertEqual(proc.stdout, "3\t" + YEAR_BOUND_LINES[2] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_WARNINGS)

    def test_max_year_since_only(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                write_year_bound_log(d), "--level", "ERROR",
                "--since", "9999-12-31T23:59:59Z",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只给 --since：含起点，输出第 4 行（最晚的合法时刻）。
        self.assertEqual(proc.stdout, "4\t" + YEAR_BOUND_LINES[3] + "\n")
        self.assertEqual(proc.stderr, EXPECTED_WARNINGS)


class NoTimeOptionTests(unittest.TestCase):
    """不传 --since / --until 时不做时间检查：六行全部输出，无警告。"""

    def test_without_time_options_all_lines_output(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(write_year_bound_log(d), "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "".join(
                f"{lineno}\t{line}\n"
                for lineno, line in enumerate(YEAR_BOUND_LINES, start=1)
            ),
        )
        self.assertEqual(proc.stderr, "")


class ParseTimestampYearBoundTests(unittest.TestCase):
    """直接验证 parse_timestamp 对年份边界的解析结果。"""

    def test_min_and_max_year_return_matching_datetime(self):
        cases = [
            ("0001-01-01T00:00:00Z", datetime(1, 1, 1, 0, 0, 0)),
            ("9999-12-31T23:59:59Z", datetime(9999, 12, 31, 23, 59, 59)),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                result = parse_timestamp(value)
                self.assertIsNotNone(result)
                # 年月日与时分秒逐项一致。
                self.assertEqual(
                    (
                        result.year, result.month, result.day,
                        result.hour, result.minute, result.second,
                    ),
                    (
                        expected.year, expected.month, expected.day,
                        expected.hour, expected.minute, expected.second,
                    ),
                )

    def test_year_zero_and_five_digits_return_none(self):
        for value in (YEAR_ZERO, YEAR_FIVE_DIGITS):
            with self.subTest(value=value):
                self.assertIsNone(parse_timestamp(value))


class InvalidYearArgumentTests(unittest.TestCase):
    """非法年份作为 --since / --until：读取文件前拒绝，退出码为 2。"""

    def test_invalid_years_rejected_as_arguments_before_reading_file(self):
        cases = [
            ("--since", YEAR_ZERO),
            ("--until", YEAR_ZERO),
            ("--since", YEAR_FIVE_DIGITS),
            ("--until", YEAR_FIVE_DIGITS),
        ]
        with tempfile.TemporaryDirectory() as d:
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
