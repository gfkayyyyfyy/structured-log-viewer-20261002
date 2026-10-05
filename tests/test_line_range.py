"""``--line-range START:END`` 物理行号区间筛选的回归测试。

需求要点：

- 区间按原始物理行号（从 1 开始）限定“匹配”，两端均包含，与级别、请求
  标识、消息子串和时间筛选取交集；命中行仍用原始行号按原文件行序输出，
  不重新编号；两端相等时只选一行。
- 端点只接受 ASCII 正十进制整数（``[0-9]+``），允许前导零，不接受符号、
  空白、空串或其他 Unicode 十进制字符；端点为零、起点大于终点、缺值、
  格式非法或重复提供该选项都在读取文件前拒绝：退出码 2，标准输出为空，
  标准错误指出 --line-range 的问题。
- 区间只限制匹配，不限制全文件诊断：范围外的损坏 JSON、顶层非对象、
  无效级别照常警告；启用时间筛选时，范围外级别合法但 timestamp 无效的
  记录也照常警告。invalid_count 仍是全文件警告数。
- 终点超过文件末尾照常筛选；起点超过文件末尾或文件为空时三种输出模式
  均无匹配输出，摘要完整且匹配数为零，退出码为 0。
- 范围外含非法 UTF-8 仍按文件读取失败处理：退出码 2，标准输出为空。
- 不传该选项时，既有筛选、输出互斥、错误处理及 iter_matches 调用约定
  完全不变。

测试输入均在用例自建的临时目录中生成并自动清理，不依赖仓库样例或外部
服务。从项目根目录执行：

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

# 验收用的五个物理行：第 1 行 ERROR 但落在范围外，第 2 行空白占行号，
# 第 3 行 ERROR 命中，第 4 行 INFO 不匹配，第 5 行非法 JSON 照常警告。
ACCEPTANCE_LINES = [
    '{"level":"ERROR"}',
    "",
    '{"level":"ERROR","message":"inside"}',
    '{"level":"INFO"}',
    "not-json",
]
ACCEPTANCE_STDERR = "第 5 行：无效日志：JSON 解析失败\n"
ACCEPTANCE_SUMMARY = {
    "matched_count": 1,
    "invalid_count": 1,
    "by_level": {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 1, "CRITICAL": 0},
}

# 各种格式非法的区间取值（端点为零与起点大于终点有独立错误文案，
# 故不在此列表中）。
MALFORMED_RANGES = [
    "", ":", "1:", ":4", "1:4:5",
    "abc", "1:x", "x:1",
    " 1:4", "1:4 ", "1 :4", "1: 4",
    "-1:4", "1:-4", "1:+4", "+1:4",
    "1:4\n", "1.5:4", "1:2e1",
    "1:٤",  # U+0664 ARABIC-INDIC DIGIT FOUR：非 ASCII 十进制字符
]


def run_cli(*args):
    """在项目根目录下运行 ``python -m log_viewer``。"""
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定。
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


def run_cli_bytes(*args):
    """按原始字节运行（用于含非法 UTF-8 输入的用例）。"""
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


class LineRangeAcceptanceTests(unittest.TestCase):
    """用户给出的验收场景：range.jsonl + --level ERROR --line-range 2:4。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "range.jsonl"
        self.path.write_bytes(
            ("\n".join(ACCEPTANCE_LINES) + "\n").encode("utf-8")
        )

    def test_default_output_only_line_three(self):
        proc = run_cli(str(self.path), "--level", "ERROR",
                       "--line-range", "2:4")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 3 行的原始行号、制表符和原文；第 1 行虽为 ERROR 但在
        # 范围外；第 4 行级别不匹配；第 2 行空白不输出。
        self.assertEqual(
            proc.stdout, "3\t" + ACCEPTANCE_LINES[2] + "\n"
        )
        # 范围外的第 5 行损坏 JSON 仍照常产生唯一一条警告。
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_summary_counts_range_match_but_filewide_invalid(self):
        proc = run_cli(str(self.path), "--level", "ERROR",
                       "--line-range", "2:4", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)
        self.assertEqual(json.loads(proc.stdout), ACCEPTANCE_SUMMARY)

    def test_jsonl_export_emits_only_raw_body_with_lf(self):
        proc = run_cli(str(self.path), "--level", "ERROR",
                       "--line-range", "2:4", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, ACCEPTANCE_LINES[2] + "\n"
        )
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)


class LineRangeBoundaryTests(unittest.TestCase):
    """闭区间端点、前导零、空白行、超出文件末尾与空文件的边界行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, lines, name="lines.jsonl", trailing_lf=True):
        path = Path(self._tmp.name) / name
        content = "\n".join(lines)
        if trailing_lf and lines:
            content += "\n"
        path.write_bytes(content.encode("utf-8"))
        return str(path)

    def test_leading_zeros_accepted(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "002:004")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "3\t" + ACCEPTANCE_LINES[2] + "\n"
        )
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_equal_endpoints_select_single_line(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "3:3")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "3\t" + ACCEPTANCE_LINES[2] + "\n"
        )

    def test_equal_endpoints_on_blank_line_outputs_nothing(self):
        # 第 2 行是空白行：区间 2:2 无匹配，但范围外的第 5 行仍警告。
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "2:2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_both_endpoints_inclusive(self):
        lines = [
            '{"level":"ERROR","message":"a"}',
            '{"level":"ERROR","message":"b"}',
            '{"level":"ERROR","message":"c"}',
        ]
        path = self._write(lines)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "1:3")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "1\t" + lines[0] + "\n"
            "2\t" + lines[1] + "\n"
            "3\t" + lines[2] + "\n",
        )
        self.assertEqual(proc.stderr, "")

    def test_end_beyond_eof_filters_normally(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "3:999")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "3\t" + ACCEPTANCE_LINES[2] + "\n"
        )
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_start_beyond_eof_no_match_but_warnings_remain(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "9:99")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_start_beyond_eof_summary_is_complete_with_zero_matches(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR",
                       "--line-range", "9:99", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            {
                "matched_count": 0,
                "invalid_count": 1,
                "by_level": {
                    "DEBUG": 0, "INFO": 0, "WARNING": 0,
                    "ERROR": 0, "CRITICAL": 0,
                },
            },
        )
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_start_beyond_eof_jsonl_output_empty(self):
        path = self._write(ACCEPTANCE_LINES)
        proc = run_cli(path, "--level", "ERROR",
                       "--line-range", "9:99", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_empty_file_all_modes(self):
        path = self._write([], trailing_lf=False)
        # 逐行与导出模式标准输出为空；摘要模式仍输出完整摘要（匹配数为零）。
        for extra in ([], ["--jsonl"]):
            with self.subTest(extra=extra):
                proc = run_cli(path, "--level", "ERROR",
                               "--line-range", "1:5", *extra)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, "")
                self.assertEqual(proc.stderr, "")
        proc = run_cli(path, "--level", "ERROR",
                       "--line-range", "1:5", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            json.loads(proc.stdout),
            {
                "matched_count": 0,
                "invalid_count": 0,
                "by_level": {
                    "DEBUG": 0, "INFO": 0, "WARNING": 0,
                    "ERROR": 0, "CRITICAL": 0,
                },
            },
        )

    def test_blank_lines_still_occupy_line_numbers(self):
        # 第 1、2 行为空白，第 3 行才是有效记录；范围按物理行号计数。
        lines = ["", "", '{"level":"ERROR","message":"x"}']
        path = self._write(lines)
        proc = run_cli(path, "--level", "ERROR", "--line-range", "3:3")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "3\t" + lines[2] + "\n")
        proc = run_cli(path, "--level", "ERROR", "--line-range", "1:2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")


class LineRangeDiagnosticsTests(unittest.TestCase):
    """范围只限制匹配：全文件诊断（警告）不受区间影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_out_of_range_broken_json_non_object_and_bad_level_warn(self):
        lines = [
            "not-json",                                  # 1 解析失败
            '["not","object"]',                          # 2 顶层非对象
            '{"message":"no level"}',                    # 3 级别无效
            '{"level":"ERROR","message":"in-range"}',    # 4 命中
        ]
        path = Path(self._tmp.name) / "diag.jsonl"
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        proc = run_cli(path, "--level", "ERROR", "--line-range", "4:4")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "4\t" + lines[3] + "\n")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：JSON 解析失败\n"
            "第 2 行：无效日志：顶层不是 JSON 对象\n"
            "第 3 行：无效日志：level 缺失或不属于支持的级别\n",
        )

    def test_out_of_range_invalid_timestamp_warns_when_time_enabled(self):
        lines = [
            '{"level":"ERROR","timestamp":"2026-01-01T00:00:00Z"}',  # 1 命中
            '{"level":"ERROR"}',                                      # 2 范围外
            "not-json",                                               # 3 范围外
            '{"level":"INFO"}',                                       # 4 范围外
        ]
        path = Path(self._tmp.name) / "time.jsonl"
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        proc = run_cli(
            path, "--level", "ERROR",
            "--since", "2026-01-01T00:00:00Z",
            "--line-range", "1:1",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "1\t" + lines[0] + "\n")
        # 启用时间筛选后，范围外级别合法但 timestamp 缺失的第 2、4 行
        # 与范围外损坏 JSON 的第 3 行都照常警告。
        self.assertEqual(
            proc.stderr,
            "第 2 行：无效日志：timestamp 缺失或格式无效\n"
            "第 3 行：无效日志：JSON 解析失败\n"
            "第 4 行：无效日志：timestamp 缺失或格式无效\n",
        )

    def test_summary_invalid_count_is_filewide(self):
        lines = ["not-json", '{"level":"ERROR","message":"x"}']
        path = Path(self._tmp.name) / "inv.jsonl"
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        proc = run_cli(path, "--level", "ERROR",
                       "--line-range", "2:2", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)

    def test_intersects_with_other_filters(self):
        lines = [
            '{"level":"ERROR","message":"hit","request_id":"r1"}',
            '{"level":"ERROR","message":"miss","request_id":"r1"}',
            '{"level":"ERROR","message":"hit","request_id":"r2"}',
            '{"level":"ERROR","message":"hit","request_id":"r1"}',
        ]
        path = Path(self._tmp.name) / "mix.jsonl"
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        proc = run_cli(
            path, "--level", "ERROR",
            "--message-contains", "hit", "--request-id", "r1",
            "--line-range", "2:4",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "4\t" + lines[3] + "\n")
        self.assertEqual(proc.stderr, "")


class LineRangeArgumentRejectionTests(unittest.TestCase):
    """非法 --line-range：读文件前拒绝，退出码 2，标准输出严格为空。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "range.jsonl"
        self.path.write_bytes(
            ("\n".join(ACCEPTANCE_LINES) + "\n").encode("utf-8")
        )
        self.missing_path = Path(self._tmp.name) / "does-not-exist.jsonl"
        self.assertFalse(self.missing_path.exists())

    def _assert_rejected_before_read(self, argv, path=None):
        path = str(path if path is not None else self.path)
        proc = run_cli(path, "--level", "ERROR", "--line-range", *argv)
        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；stderr={proc.stderr!r}",
        )
        self.assertEqual(proc.stdout, "")
        self.assertIn("--line-range", proc.stderr)
        self.assertNotIn("文件读取失败", proc.stderr)
        self.assertNotIn("无效日志", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_malformed_values_rejected(self):
        for value in MALFORMED_RANGES:
            with self.subTest(value=value):
                self._assert_rejected_before_read([value])

    def test_zero_endpoints_rejected(self):
        for value in ("0:4", "4:0", "0:0", "00:004"):
            with self.subTest(value=value):
                self._assert_rejected_before_read([value])

    def test_start_greater_than_end_rejected(self):
        for value in ("4:2", "100:99"):
            with self.subTest(value=value):
                self._assert_rejected_before_read([value])

    def test_repeated_option_rejected(self):
        proc = run_cli(
            str(self.path), "--level", "ERROR",
            "--line-range", "2:4", "--line-range", "1:3",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("--line-range", proc.stderr)

    def test_rejected_before_file_read_even_if_path_missing(self):
        # 文件路径不存在时仍先报告 --line-range 参数错误。
        self._assert_rejected_before_read(["4:2"], path=self.missing_path)

    def test_missing_value_is_argument_error(self):
        proc = run_cli(str(self.path), "--level", "ERROR", "--line-range")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("--line-range", proc.stderr)

    def test_range_validation_after_other_parameter_checks(self):
        # 既有参数校验优先级保持不变：--level 非法或 --since 非法时，
        # 即使 --line-range 同时非法也只报告先序参数的错误。
        proc = run_cli(
            str(self.missing_path), "--level", "TRACE",
            "--line-range", "4:2",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("--level", proc.stderr)
        self.assertNotIn("--line-range", proc.stderr)

        proc = run_cli(
            str(self.missing_path), "--level", "ERROR",
            "--since", "bad", "--line-range", "4:2",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("--since", proc.stderr)
        self.assertNotIn("--line-range", proc.stderr)


class LineRangeReadFailureTests(unittest.TestCase):
    """范围外的非法 UTF-8 仍导致整文件读取失败。"""

    def test_invalid_utf8_outside_range_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.jsonl"
            # 第 1、2 行合法（范围 1:2 内），第 3 行是非法字节 0xFF。
            path.write_bytes(
                b'{"level":"ERROR"}\n{"level":"ERROR"}\n\xff\n'
            )
            proc = run_cli_bytes(
                str(path), "--level", "ERROR", "--line-range", "1:2"
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, b"")
            self.assertIn("文件读取失败".encode("utf-8"), proc.stderr)
            self.assertIn(os.fsencode(str(path)), proc.stderr)


class LineRangeIterMatchesTests(unittest.TestCase):
    """iter_matches 的进程内调用约定。"""

    LINES = [
        '{"level":"ERROR","message":"a"}',
        "",
        '{"level":"INFO"}',
        '{"level":"ERROR","message":"b"}',
        "not-json",
    ]

    def test_none_line_range_matches_legacy_call(self):
        # 不传与显式传 None 行为一致：全部 ERROR 命中（第 1、4 行），
        # 警告仍是第 5 行。
        legacy_matches, legacy_warnings = iter_matches(self.LINES, "ERROR")
        none_matches, none_warnings = iter_matches(
            self.LINES, "ERROR", line_range=None
        )
        self.assertEqual(legacy_matches, none_matches)
        self.assertEqual(legacy_warnings, none_warnings)
        self.assertEqual(
            legacy_matches,
            [(1, self.LINES[0]), (4, self.LINES[3])],
        )
        self.assertEqual(legacy_warnings, [(5, "无效日志：JSON 解析失败")])

    def test_line_range_restricts_matches_only(self):
        matches, warnings = iter_matches(
            self.LINES, "ERROR", line_range=(3, 4)
        )
        # 只有第 4 行落在 [3, 4] 且为 ERROR；行号不重新编号。
        self.assertEqual(matches, [(4, self.LINES[3])])
        # 警告不受区间影响：第 5 行仍在。
        self.assertEqual(warnings, [(5, "无效日志：JSON 解析失败")])

    def test_line_range_equal_endpoints(self):
        matches, warnings = iter_matches(
            self.LINES, "ERROR", line_range=(1, 1)
        )
        self.assertEqual(matches, [(1, self.LINES[0])])
        self.assertEqual(warnings, [(5, "无效日志：JSON 解析失败")])

    def test_line_range_start_beyond_eof(self):
        matches, warnings = iter_matches(
            self.LINES, "ERROR", line_range=(9, 99)
        )
        self.assertEqual(matches, [])
        self.assertEqual(warnings, [(5, "无效日志：JSON 解析失败")])


if __name__ == "__main__":
    unittest.main()
