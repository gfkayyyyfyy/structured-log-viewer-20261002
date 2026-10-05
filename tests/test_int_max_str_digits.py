"""整数转换位数限制（PYTHONINTMAXSTRDIGITS）下超长整数的回归测试。

Python 3.11 起，标准库对“十进制字符串 -> int”的转换默认施加位数上限
（可用 PYTHONINTMAXSTRDIGITS 环境变量或 sys.set_int_max_str_digits
调整，0 表示关闭）。当 JSON 行中出现未加引号、位数超过该上限的整数时，
json.loads 在数字扫描阶段抛出的是普通 ValueError（消息为
“Exceeds the limit ... for integer string conversion”），而不是
json.JSONDecodeError。本工具必须把它与普通语法错误一样按既有无效日志
规则处理：该物理行跳过、按原始行号在标准错误输出一条
“无效日志：JSON 解析失败”警告、前后有效记录照常处理、退出码为 0、
不输出异常堆栈；即使该行级别不在所选范围也只报告这一条解析失败，
不叠加级别或时间戳警告。

验收夹具（UTF-8、四行，第三行为空行，空行仍占原始行号）：

  1. {"level":"ERROR","message":"before"}
  2. {"level":"ERROR","payload":<5000 个连续数字 9，未加引号>}
  3. （空行）
  4. {"level":"ERROR","message":"after"}

在 PYTHONINTMAXSTRDIGITS=4300 下：第 2 行为 5000 位整数，超过上限，
按 JSON 解析失败处理；4300 位整数恰好不超限，带引号的 5000 位数字
字符串不触发整数转换，二者都沿用原行为正常命中；把限制调到 0（关闭）
后，5000 位整数恢复为可解析的正常记录。

命令行用例按子进程运行（需要环境变量在解释器启动前生效），直接调用
iter_matches 的用例在进程内用 sys.set_int_max_str_digits 设置并在
清理时还原。运行环境不支持该限制（Python 3.10 及以下，或发行版禁用
了该机制导致环境变量不生效）时整体跳过：跳过是环境能力问题，不代表
行为通过或失败。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from log_viewer import iter_matches

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INT_DIGIT_LIMIT = 4300
OVER_LIMIT_DIGITS = 5000
AT_LIMIT_DIGITS = INT_DIGIT_LIMIT

LINE1 = '{"level":"ERROR","message":"before"}'
# 顶层 level 为 ERROR，payload 是未加引号的 5000 位整数。
BIG_INT_LINE = '{"level":"ERROR","payload":' + "9" * OVER_LIMIT_DIGITS + "}"
BLANK_LINE = ""
LINE4 = '{"level":"ERROR","message":"after"}'
ACCEPTANCE_LINES = [LINE1, BIG_INT_LINE, BLANK_LINE, LINE4]

EXPECTED_WARNING = "第 2 行：无效日志：JSON 解析失败"


def _run_env():
    """构造子进程环境：固定输出编码，并施加整数位数限制。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["PYTHONINTMAXSTRDIGITS"] = str(INT_DIGIT_LIMIT)
    return env


def _int_limit_env_takes_effect():
    """探测当前解释器是否真的接受 PYTHONINTMAXSTRDIGITS 限制。

    某些 3.11+ 发行版可能在构建时禁用了该机制；探测不通过则跳过，
    避免在无法构造失败前提的环境里产生误导性断言。
    """
    if not hasattr(sys, "set_int_max_str_digits"):
        return False
    probe = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; print(sys.get_int_max_str_digits())",
        ],
        capture_output=True,
        text=True,
        env=_run_env(),
    )
    return probe.returncode == 0 and probe.stdout.strip() == str(INT_DIGIT_LIMIT)


LIMIT_SUPPORTED = _int_limit_env_takes_effect()
requires_int_limit = unittest.skipUnless(
    LIMIT_SUPPORTED,
    "需要 Python 3.11+ 且 PYTHONINTMAXSTRDIGITS 环境变量实际生效",
)


def run_cli(path, *extra, digits=INT_DIGIT_LIMIT, binary=False):
    """在项目根目录运行 ``python -m log_viewer``，返回完成的进程。

    digits 为子进程的 PYTHONINTMAXSTRDIGITS 值（0 表示关闭限制）。
    binary=True 时按原始字节捕获（--jsonl 逐字节断言 LF）。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["PYTHONINTMAXSTRDIGITS"] = str(digits)
    kwargs = dict(cwd=str(PROJECT_ROOT), env=env)
    if not binary:
        kwargs.update(text=True, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", str(path), "--level", "ERROR",
         *extra],
        capture_output=True,
        **kwargs,
    )


@requires_int_limit
class OverLimitIntegerLineTests(unittest.TestCase):
    """5000 位整数行在 4300 限制下按 JSON 解析失败处理：行号、前后记录、
    空行占位、退出码与三种输出模式。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, lines, name="limit.jsonl", final_newline=True):
        path = Path(self._tmp.name) / name
        payload = "\n".join(lines)
        if final_newline:
            payload += "\n"
        path.write_text(payload, encoding="utf-8")
        return path

    def test_fixture_shapes(self):
        # 夹具自检：第 2 行确为 5000 位未加引号整数，第 3 行为空行。
        self.assertEqual(len(ACCEPTANCE_LINES), 4)
        self.assertTrue(BIG_INT_LINE.startswith('{"level":"ERROR","payload":9'))
        self.assertNotIn('"payload":"', BIG_INT_LINE)
        self.assertEqual(BIG_INT_LINE.count("9"), OVER_LIMIT_DIGITS)
        self.assertEqual(ACCEPTANCE_LINES[2], "")

    def test_default_mode_skips_bad_line_keeps_neighbors(self):
        for label, final in (("末行有换行", True), ("末行无换行", False)):
            with self.subTest(label):
                path = self._write(ACCEPTANCE_LINES, final_newline=final)
                proc = run_cli(path)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                # 只输出第 1、4 行：行号、制表符、完整原文；空行使第 4 行
                # 行号保持为 4，坏行不输出。
                self.assertEqual(
                    proc.stdout,
                    f"1\t{LINE1}\n4\t{LINE4}\n",
                )
                # 标准错误只有第 2 行的一条 JSON 解析失败警告。
                self.assertEqual(proc.stderr, EXPECTED_WARNING + "\n")
                self.assertNotIn("Traceback", proc.stderr)

    def test_summary_counts_two_matches_one_invalid(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 2,
             "CRITICAL": 0},
        )
        self.assertEqual(proc.stderr, EXPECTED_WARNING + "\n")

    def test_jsonl_export_contains_only_raw_matching_records(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--jsonl", binary=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 仅两条匹配记录原文，各补一个 LF；坏行不进入导出结果。
        self.assertEqual(
            proc.stdout,
            LINE1.encode("utf-8") + b"\n" + LINE4.encode("utf-8") + b"\n",
        )
        self.assertEqual(
            proc.stderr.decode("utf-8"), EXPECTED_WARNING + "\n"
        )

    def test_parse_failure_warns_even_when_its_level_not_selected(self):
        # 坏行级别为 INFO（不在 --level ERROR 范围内）：解析失败仍只报告
        # 一条，且后续 ERROR 记录照常输出，不叠加级别或时间戳警告。
        # 第 2 行带合法 timestamp，避免它自身在启用 --since 时按既有规则
        # 产生“timestamp 缺失”警告而干扰对坏行警告条数的断言。
        lines = [
            '{"level":"INFO","payload":' + "9" * OVER_LIMIT_DIGITS + "}",
            '{"level":"ERROR","message":"after",'
            '"timestamp":"2026-01-02T00:00:00Z"}',
        ]
        path = self._write(lines, name="level-mismatch.jsonl")
        proc = run_cli(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"2\t{lines[1]}\n")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：JSON 解析失败\n",
        )
        # 即使启用时间筛选，坏行也在解析阶段即被跳过，只此一条警告，
        # 不会再叠加 timestamp 或 level 警告。
        proc = run_cli(path, "--since", "2026-01-01T00:00:00Z")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：JSON 解析失败\n",
        )
        self.assertNotIn("timestamp", proc.stderr)
        self.assertNotIn("level", proc.stderr)

    def test_ordinary_syntax_error_still_uses_same_warning(self):
        # 既有 JSONDecodeError 路径的文案与计数不变。
        lines = [LINE1, "not-json", LINE4]
        path = self._write(lines, name="syntax.jsonl")
        proc = run_cli(path, "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            proc.stderr, "第 2 行：无效日志：JSON 解析失败\n"
        )


@requires_int_limit
class NonTriggeringIntegerShapesTests(unittest.TestCase):
    """不触发运行环境限制的数字沿用原行为，不按正文长度拒绝记录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, line, name="case.jsonl"):
        path = Path(self._tmp.name) / name
        path.write_text(line + "\n", encoding="utf-8")
        return path

    def test_quoted_long_digit_string_matches(self):
        line = '{"level":"ERROR","payload":"' + "9" * OVER_LIMIT_DIGITS + '"}'
        path = self._write(line, "quoted.jsonl")
        proc = run_cli(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"1\t{line}\n")
        self.assertEqual(proc.stderr, "")

    def test_integer_exactly_at_limit_matches(self):
        line = '{"level":"ERROR","n":' + "9" * AT_LIMIT_DIGITS + "}"
        path = self._write(line, "at-limit.jsonl")
        proc = run_cli(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"1\t{line}\n")
        self.assertEqual(proc.stderr, "")

    def test_over_limit_integer_matches_when_limit_disabled(self):
        # PYTHONINTMAXSTRDIGITS=0 关闭限制：同一行恢复为可解析的正常记录。
        path = self._write(BIG_INT_LINE, "disabled.jsonl")
        proc = run_cli(path, digits=0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"1\t{BIG_INT_LINE}\n")
        self.assertEqual(proc.stderr, "")


@requires_int_limit
class IterMatchesApiTests(unittest.TestCase):
    """直接调用 iter_matches：失败进入 warnings，两组 (行号, 文本) 列表
    的返回结构与调用约定保持兼容。"""

    def setUp(self):
        # 在进程内施加同样的位数限制，用例结束后还原运行环境原值。
        self._previous = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(INT_DIGIT_LIMIT)
        self.addCleanup(sys.set_int_max_str_digits, self._previous)

    def test_over_limit_line_enters_warnings_and_neighbors_match(self):
        matches, warnings = iter_matches(ACCEPTANCE_LINES, "ERROR")
        self.assertEqual(matches, [(1, LINE1), (4, LINE4)])
        self.assertEqual(warnings, [(2, "无效日志：JSON 解析失败")])

    def test_warning_reported_when_level_outside_selection(self):
        # 级别序列不含 ERROR 时坏行依旧进 warnings，matches 为空。
        matches, warnings = iter_matches(ACCEPTANCE_LINES, ("INFO",))
        self.assertEqual(matches, [])
        self.assertEqual(warnings, [(2, "无效日志：JSON 解析失败")])

    def test_blank_line_keeps_physical_number_but_is_not_invalid(self):
        # 空行（第 3 行）既不在 matches 也不在 warnings；行号仅由第 4 行体现。
        matches, warnings = iter_matches(ACCEPTANCE_LINES, "ERROR")
        linenos = [n for n, _ in matches] + [n for n, _ in warnings]
        self.assertNotIn(3, linenos)
        self.assertEqual([n for n, _ in matches], [1, 4])

    def test_quoted_string_and_at_limit_integer_still_match(self):
        quoted = '{"level":"ERROR","payload":"' + "9" * OVER_LIMIT_DIGITS + '"}'
        at_limit = '{"level":"ERROR","n":' + "9" * AT_LIMIT_DIGITS + "}"
        matches, warnings = iter_matches([quoted, at_limit], "ERROR")
        self.assertEqual(matches, [(1, quoted), (2, at_limit)])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
