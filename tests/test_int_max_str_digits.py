"""JSON 整数转换位数限制（PYTHONINTMAXSTRDIGITS）导致解析失败的回归测试。

Python 3.11 起解释器限制十进制整数字符串转换的最大位数
（sys.get_int_max_str_digits，可用环境变量 PYTHONINTMAXSTRDIGITS 调整）。
当 JSON 行中含有超过该限制的未加引号整数时，json.loads 抛出的不是
JSONDecodeError，而是普通 ValueError；查看器必须把这类物理行按既有的
“无效日志：JSON 解析失败”规则处理：跳过该行、按原始行号向标准错误输出
一条警告、不输出异常堆栈、前后有效记录照常筛选、正常完成的退出码为 0。

本模块用 PYTHONINTMAXSTRDIGITS=4300 的子进程环境复核命令行行为，
并用 sys.set_int_max_str_digits 复核 iter_matches 的进程内行为。
仅在支持该限制的 Python 3.11+ 上运行；更早版本整组跳过。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from log_viewer import iter_matches

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 5000 个连续数字 9：超过 4300 位的限制，触发 ValueError。
LONG_DIGITS = "9" * 5000

# 验收用例的四个物理行：有效 ERROR、超限整数行、空行、有效 ERROR。
LIMIT_LINES = [
    '{"level":"ERROR","message":"before"}',
    '{"level":"ERROR","payload":' + LONG_DIGITS + "}",
    "",
    '{"level":"ERROR","message":"after"}',
]

EXPECTED_STDOUT = (
    "1\t" + LIMIT_LINES[0] + "\n"
    "4\t" + LIMIT_LINES[3] + "\n"
)
EXPECTED_STDERR = "第 2 行：无效日志：JSON 解析失败\n"


def run_cli(*args):
    """在限制位数为 4300 的环境下运行 ``python -m log_viewer``。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["PYTHONINTMAXSTRDIGITS"] = "4300"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


@unittest.skipUnless(
    sys.version_info >= (3, 11),
    "整数转换位数限制需要 Python 3.11+",
)
class IntMaxStrDigitsCliTests(unittest.TestCase):
    """命令行：超限整数行按无效日志处理，不中断整个文件。"""

    def setUp(self):
        self.tmp = self.id().replace(".", "_")

    def _write(self, lines) -> str:
        path = Path(os.environ.get("TEMP", "/tmp")) / (self.tmp + ".jsonl")
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        self.addCleanup(lambda p=path: p.exists() and p.unlink())
        return str(path)

    def test_over_limit_integer_line_is_skipped_with_single_warning(self):
        path = self._write(LIMIT_LINES)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        # 只有第 1、4 行的行号、制表符和完整原文。
        self.assertEqual(proc.stdout, EXPECTED_STDOUT)
        # 标准错误只有第 2 行的一条解析失败警告，没有异常堆栈；
        # 空行（第 3 行）不占警告。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_summary_counts_over_limit_line_as_invalid(self):
        path = self._write(LIMIT_LINES)
        proc = run_cli(path, "--level", "ERROR", "--summary")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 2, "CRITICAL": 0},
        )

    def test_warning_reported_even_when_level_not_selected(self):
        # 解析失败发生在级别判断之前：即使该行的级别不在所选范围，
        # 仍只报告一条解析失败警告，不叠加级别或时间戳警告。
        path = self._write(LIMIT_LINES)
        proc = run_cli(path, "--level", "DEBUG")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_jsonl_export_excludes_failed_line(self):
        path = self._write(LIMIT_LINES)
        proc = run_cli(path, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        # 导出只含匹配记录原文并补换行，失败行不进入导出结果。
        self.assertEqual(
            proc.stdout,
            LIMIT_LINES[0] + "\n" + LIMIT_LINES[3] + "\n",
        )

    def test_quoted_long_digits_and_in_limit_integer_unchanged(self):
        # 带引号的长数字字符串与未触发限制的整数沿用原行为：
        # 不按正文长度拒绝记录，不产生警告。
        lines = [
            '{"level":"ERROR","message":"' + LONG_DIGITS + '"}',
            '{"level":"ERROR","payload":' + "9" * 4300 + "}",
        ]
        path = self._write(lines)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            proc.stdout,
            "1\t" + lines[0] + "\n" + "2\t" + lines[1] + "\n",
        )


@unittest.skipUnless(
    sys.version_info >= (3, 11),
    "整数转换位数限制需要 Python 3.11+",
)
class IntMaxStrDigitsIterMatchesTests(unittest.TestCase):
    """iter_matches：这类失败进入 warnings，返回结构保持兼容。"""

    def test_failure_goes_to_warnings(self):
        old_limit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            matches, warnings = iter_matches(LIMIT_LINES, "ERROR")
        finally:
            sys.set_int_max_str_digits(old_limit)
        self.assertEqual(
            matches,
            [(1, LIMIT_LINES[0]), (4, LIMIT_LINES[3])],
        )
        self.assertEqual(warnings, [(2, "无效日志：JSON 解析失败")])


if __name__ == "__main__":
    unittest.main()
