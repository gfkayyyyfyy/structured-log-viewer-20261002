"""--line-range START:END 行号区间筛选的回归测试。

区间按原始物理行号限定匹配结果：两端均包含，与级别等其余筛选条件取
交集，按原文件顺序输出且不重新编号；两端相等时只选一行。端点只接受
ASCII 正十进制整数（允许前导零，不接受符号或空白）。缺值、空值、格式
非法、端点为零、起点大于终点或重复提供选项，都在读取文件之前拒绝：
标准输出为空，标准错误指出 --line-range 的问题，退出码为 2。

范围只限制匹配，全文件诊断不变：范围外的损坏 JSON、顶层非对象及无效
级别照常警告；启用时间筛选时，范围外级别合法但 timestamp 无效的记录
也照常警告。matched_count 和 by_level 只统计范围内满足其他条件的记录，
invalid_count 仍为全文件警告数。终点超过文件末尾仍正常筛选；起点超过
末尾或文件为空时无匹配，摘要完整且匹配数为零，退出码为 0。读取失败
（包括范围外含非法 UTF-8）仍使标准输出为空、退出码为 2。

验收场景：range.jsonl 五行依次为 {"level":"ERROR"}、空行、
{"level":"ERROR","message":"inside"}、{"level":"INFO"} 和 not-json。
--level ERROR --line-range 2:4 只输出第 3 行原文及行号，标准错误仅有
第 5 行的 JSON 解析失败警告；加 --summary 后 matched_count 为 1、
invalid_count 为 1、by_level 中 ERROR 为 1，两次退出码均为 0。

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# print 写标准输出/标准错误时的平台记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 验收用例的五个物理行：有效 ERROR、空行、带 message 的 ERROR、INFO、
# 损坏 JSON。空行仍占行号：第 3 行的匹配证明行号按物理行计算。
RANGE_LINES = [
    '{"level":"ERROR"}',
    "",
    '{"level":"ERROR","message":"inside"}',
    '{"level":"INFO"}',
    "not-json",
]

WARNING_LINE_5 = "第 5 行：无效日志：JSON 解析失败"

# 当前源码中各 --line-range 参数错误的完整文案（__main__.py 中的 print 输出）。
ERROR_FORMAT = (
    "参数错误：--line-range 必须是 START:END 形式，"
    "两端为不含符号和空白的正十进制整数"
)
ERROR_ZERO = "参数错误：--line-range 的端点不能为零"
ERROR_ORDER = "参数错误：--line-range 的起点不能大于终点"
ERROR_DUPLICATE = "参数错误：--line-range 只能提供一次"


def run_cli(*args):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class LineRangeTestCase(unittest.TestCase):
    """在临时目录中准备 JSONL 文件的公共基类。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, lines, trailing_lf=True):
        path = self.dir / name
        text = "\n".join(lines)
        if trailing_lf:
            text += "\n"
        path.write_bytes(text.encode("utf-8"))
        return str(path)

    def _write_range_file(self):
        return self._write("range.jsonl", RANGE_LINES)


class LineRangeFilterTests(LineRangeTestCase):
    """区间与其余筛选条件取交集，只限制匹配结果。"""

    def test_acceptance_default_output(self):
        path = self._write_range_file()
        proc = run_cli(path, "--level", "ERROR", "--line-range", "2:4")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 3 行：第 1 行的 ERROR 在范围外静默不输出，
        # 空行（第 2 行）仍占行号，行号不重新编号。
        self.assertEqual(
            proc.stdout,
            ("3\t" + RANGE_LINES[2]).encode("utf-8") + RECORD_TERMINATOR,
        )
        # 全文件诊断不变：范围外第 5 行的损坏 JSON 照常警告。
        self.assertEqual(
            proc.stderr,
            WARNING_LINE_5.encode("utf-8") + RECORD_TERMINATOR,
        )

    def test_acceptance_summary(self):
        path = self._write_range_file()
        proc = run_cli(
            path, "--level", "ERROR", "--line-range", "2:4", "--summary"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stderr,
            WARNING_LINE_5.encode("utf-8") + RECORD_TERMINATOR,
        )
        summary = json.loads(proc.stdout.decode("utf-8"))
        # matched_count 与 by_level 只统计范围内记录；
        # invalid_count 仍为全文件警告数。
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 1, "CRITICAL": 0},
        )

    def test_jsonl_export_outputs_raw_body_with_lf(self):
        path = self._write_range_file()
        proc = run_cli(path, "--level", "ERROR", "--line-range", "2:4",
                       "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stderr,
            WARNING_LINE_5.encode("utf-8") + RECORD_TERMINATOR,
        )
        # 只输出命中的原始正文并补 LF：不带行号与制表符前缀，不改写内容。
        self.assertEqual(
            proc.stdout, (RANGE_LINES[2] + "\n").encode("utf-8")
        )

    def test_equal_endpoints_select_single_line(self):
        path = self._write_range_file()
        proc = run_cli(path, "--level", "ERROR", "--line-range", "1:1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            ("1\t" + RANGE_LINES[0]).encode("utf-8") + RECORD_TERMINATOR,
        )

    def test_leading_zeros_accepted(self):
        path = self._write_range_file()
        proc = run_cli(path, "--level", "ERROR", "--line-range", "02:04")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            ("3\t" + RANGE_LINES[2]).encode("utf-8") + RECORD_TERMINATOR,
        )

    def test_end_beyond_file_end_still_filters(self):
        path = self._write_range_file()
        proc = run_cli(path, "--level", "INFO", "--line-range", "4:999")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            ("4\t" + RANGE_LINES[3]).encode("utf-8") + RECORD_TERMINATOR,
        )

    def test_start_beyond_file_end_matches_nothing(self):
        path = self._write_range_file()
        proc = run_cli(
            path, "--level", "ERROR", "--line-range", "6:10", "--summary"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 逐行及导出输出为空；摘要完整且匹配数为零；
        # 全文件警告（第 5 行）照常。
        self.assertEqual(
            proc.stderr,
            WARNING_LINE_5.encode("utf-8") + RECORD_TERMINATOR,
        )
        summary = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(summary["matched_count"], 0)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0},
        )

    def test_empty_file_matches_nothing(self):
        path = self._write("empty.jsonl", [], trailing_lf=False)
        proc = run_cli(
            path, "--level", "ERROR", "--line-range", "1:5", "--summary"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        summary = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(summary["matched_count"], 0)
        self.assertEqual(summary["invalid_count"], 0)

    def test_out_of_range_timestamp_warning_preserved(self):
        # 启用时间筛选时，范围外级别合法但 timestamp 无效的记录照常警告。
        lines = [
            '{"level":"ERROR","timestamp":"bad"}',
            '{"level":"ERROR","timestamp":"2026-01-01T00:00:00Z"}',
        ]
        path = self._write("ts.jsonl", lines)
        proc = run_cli(
            path, "--level", "ERROR",
            "--since", "2025-01-01T00:00:00Z",
            "--line-range", "2:2",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            ("2\t" + lines[1]).encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
            + RECORD_TERMINATOR,
        )

    def test_undecodable_bytes_out_of_range_still_read_failure(self):
        # 非法 UTF-8 即使在范围外，整份文件仍读取失败：
        # 标准输出为空，退出码为 2。
        path = self.dir / "bad.jsonl"
        path.write_bytes(b'{"level":"ERROR"}\n\xff')
        proc = run_cli(str(path), "--level", "ERROR", "--line-range", "1:1")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("文件读取失败".encode("utf-8"), proc.stderr)

    def test_huge_endpoints_not_limited_by_int_max_str_digits(self):
        # 端点不经过 int 转换：5000 位十进制端点（超过默认 4300 位限制）
        # 仍是合法端点，按数值远超文件行数处理。
        path = self._write_range_file()
        huge_end = "9" * 5000
        proc = run_cli(
            path, "--level", "ERROR", "--line-range", "2:" + huge_end
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            ("3\t" + RANGE_LINES[2]).encode("utf-8") + RECORD_TERMINATOR,
        )
        # 超大端点之间仍按数值比较起点与终点。
        proc = run_cli(
            path, "--level", "ERROR",
            "--line-range", "9" * 5000 + ":" + "8" * 5000,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr, ERROR_ORDER.encode("utf-8") + RECORD_TERMINATOR
        )


class LineRangeValidationTests(LineRangeTestCase):
    """非法区间在读取文件之前拒绝：退出码 2，标准输出为空。"""

    def _assert_rejected(self, argv_suffix, expected_message):
        # 路径确定不存在：若校验未在读文件前拒绝，错误文案会是文件读取失败。
        missing = str(self.dir / "missing.jsonl")
        proc = run_cli(missing, "--level", "ERROR", *argv_suffix)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr,
            expected_message.encode("utf-8") + RECORD_TERMINATOR,
        )

    def test_malformed_values_rejected(self):
        bad_values = [
            "",        # 空值
            "   ",     # 全空白
            "1",       # 缺少冒号与终点
            "1:",      # 缺终点
            ":2",      # 缺起点
            ":",       # 两端都缺
            "1:2:3",   # 多余冒号
            "+1:2",    # 符号
            "-1:2",    # 负号
            " 1:2",    # 首尾空白
            "1:2 ",
            "1: 2",    # 内部空白
            "1.5:2",   # 小数
            "1e3:5",   # 科学计数法
            "１:２",      # 全角数字不是 ASCII 数字
        ]
        for value in bad_values:
            with self.subTest(值=value):
                self._assert_rejected(["--line-range", value], ERROR_FORMAT)

    def test_zero_endpoint_rejected(self):
        for value in ["0:3", "3:0", "0:0", "00:3", "3:00"]:
            with self.subTest(值=value):
                self._assert_rejected(["--line-range", value], ERROR_ZERO)

    def test_start_greater_than_end_rejected(self):
        self._assert_rejected(["--line-range", "4:2"], ERROR_ORDER)
        # 前导零不影响数值比较。
        self._assert_rejected(["--line-range", "04:2"], ERROR_ORDER)

    def test_duplicate_option_rejected(self):
        self._assert_rejected(
            ["--line-range", "1:2", "--line-range", "3:4"],
            ERROR_DUPLICATE,
        )

    def test_missing_value_rejected_by_argparse(self):
        path = self._write_range_file()
        proc = run_cli(path, "--level", "ERROR", "--line-range")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("--line-range".encode("utf-8"), proc.stderr)

    def test_validation_precedes_file_read(self):
        # 文件存在且合法：非法区间仍在输出任何内容之前被拒绝。
        path = self._write_range_file()
        proc = run_cli(path, "--level", "ERROR", "--line-range", "0:2")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr, ERROR_ZERO.encode("utf-8") + RECORD_TERMINATOR
        )


if __name__ == "__main__":
    unittest.main()
