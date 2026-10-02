"""log_viewer 命令行流程的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempname / json），
测试输入均在用例自建的临时目录中生成，不依赖 sample.jsonl 或外部服务。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from log_viewer import iter_matches, normalize_level, parse_timestamp

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 核心示例的五个物理行：
# 第 1 行有效但不匹配，第 2 行空白，第 3 行非法 JSON，
# 第 4、5 行匹配 ERROR。
CORE_LINES = [
    '{"level":"INFO"}',
    "",
    "not-json",
    '{"level":" error ","message":"失败"}',
    '{"level":"ERROR","message":"第二条"}',
]
CORE_STDOUT = (
    "4\t" + CORE_LINES[3] + "\n"
    "5\t" + CORE_LINES[4] + "\n"
)
CORE_STDERR = "第 3 行：无效日志：JSON 解析失败\n"


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


class CoreFilteringTests(unittest.TestCase):
    """--level 筛选的主流程：匹配、警告、行号、原序。"""

    def setUp(self):
        self.tmp = self.id().replace(".", "_")

    def _write(self, content: bytes) -> str:
        # 每个用例独立文件名，便于人工排查临时目录。
        path = Path(os.environ.get("TEMP", "/tmp")) / (self.tmp + ".jsonl")
        path.write_bytes(content)
        self.addCleanup(lambda p=path: p.exists() and p.unlink())
        return str(path)

    def test_core_example_lf_crlf_and_without_final_newline(self):
        payloads = {
            "LF，末行有换行": ("\n".join(CORE_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(CORE_LINES) + "\r\n").encode("utf-8"),
            "末行无换行符": "\n".join(CORE_LINES).encode("utf-8"),
        }
        for label, payload in payloads.items():
            with self.subTest(label):
                path = self._write(payload)
                proc = run_cli(path, "--level", " eRrOr ")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, CORE_STDOUT)
                self.assertEqual(proc.stderr, CORE_STDERR)

    def test_matched_line_keeps_surrounding_spaces_and_chinese(self):
        line1 = '  {"level":"ERROR","message":" 中文内容 "}  '
        line2 = ""  # 空白行占位行号
        line3 = '\t{"level":"ERROR","message":"制表符与中文"}\t'
        path = self._write(
            ("\n".join([line1, line2, line3]) + "\n").encode("utf-8")
        )
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        # 匹配行首尾空格、行内制表符与中文均原样保留，行号仍为原始物理行号。
        self.assertEqual(
            proc.stdout,
            "1\t" + line1 + "\n"
            "3\t" + line3 + "\n",
        )


class InvalidRecordTests(unittest.TestCase):
    """各类无效非空行各产生一条带行号的警告，后续有效行仍输出。"""

    def test_each_invalid_nonempty_line_warns_with_lineno(self):
        lines = [
            '{"level":"ERROR","message":"开头"}',   # 1 有效，输出
            '["level", "ERROR"]',                    # 2 顶层数组
            '{"message":"缺少 level"}',              # 3 缺少 level
            '{"level":42}',                          # 4 非字符串 level
            '{"level":"VERBOSE"}',                   # 5 不支持的级别
            '{"level":"ERROR","message":"结尾"}',    # 6 有效，仍输出
        ]
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "invalid.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(str(path), "--level", "ERROR")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "1\t" + lines[0] + "\n"
            "6\t" + lines[5] + "\n",
        )
        self.assertEqual(
            proc.stderr,
            "第 2 行：无效日志：顶层不是 JSON 对象\n"
            "第 3 行：无效日志：level 缺失或不属于支持的级别\n"
            "第 4 行：无效日志：level 缺失或不属于支持的级别\n"
            "第 5 行：无效日志：level 缺失或不属于支持的级别\n",
        )


class EndStateTests(unittest.TestCase):
    """流程结束结果：空输入、无匹配、仅无效行等。"""

    def _run_with_content(self, content: bytes, level="ERROR"):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.jsonl"
            path.write_bytes(content)
            return run_cli(str(path), "--level", level)

    def test_empty_file(self):
        proc = self._run_with_content(b"")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_only_blank_lines(self):
        # LF 空行、纯空格、纯制表符以及 CRLF 空行都占行号但不产生警告。
        proc = self._run_with_content("\n  \n\t\n\r\n".encode("utf-8"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_all_records_non_matching(self):
        content = '{"level":"INFO"}\n{"level":"WARNING"}\n'.encode("utf-8")
        proc = self._run_with_content(content)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_only_invalid_lines(self):
        content = b"not-json\n{}\n"
        proc = self._run_with_content(content)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        # 警告按行号递增。
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：JSON 解析失败\n"
            "第 2 行：无效日志：level 缺失或不属于支持的级别\n",
        )


class ArgumentErrorTests(unittest.TestCase):
    """参数问题：退出码 2，标准输出为空，标准错误含参数错误信息。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "log.jsonl")
        Path(self.path).write_text(
            '{"level":"ERROR"}\n', encoding="utf-8"
        )

    def test_missing_level_argument(self):
        proc = run_cli(self.path)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # argparse 的用法与错误说明中包含选项名 --level。
        self.assertIn("--level", proc.stderr)

    def test_unsupported_level_value(self):
        proc = run_cli(self.path, "--level", "trace")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("参数错误", proc.stderr)


class FileReadErrorTests(unittest.TestCase):
    """文件读取失败：退出码 2，标准输出为空，标准错误含读取失败信息。"""

    def test_path_does_not_exist(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            proc = run_cli(missing, "--level", "ERROR")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("文件读取失败", proc.stderr)

    def test_path_is_a_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(d, "--level", "ERROR")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("文件读取失败", proc.stderr)

    def test_file_contains_invalid_utf8(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad-utf8.jsonl"
            path.write_bytes(b'{"level":"ERROR"}\xff\xfe\n')
            proc = run_cli(str(path), "--level", "ERROR")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # 只固定前缀，不绑定操作系统附加的错误细节。
        self.assertIn("文件读取失败", proc.stderr)


class RequestIdFilterTests(unittest.TestCase):
    """可选 --request-id：级别与请求标识同时满足才输出，坏行警告不受影响。"""

    def _run(self, tmpdir, lines, *extra):
        path = Path(tmpdir) / "requests.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return run_cli(str(path), "--level", "ERROR", *extra)

    def test_level_and_request_id_must_both_match(self):
        import tempfile
        lines = [
            '{"level":"INFO","request_id":"r-1"}',
            '{"level":"ERROR","request_id":"r-2"}',
            '{"level":"ERROR","request_id":"r-1"}',
            "not-json",
            '{"level":"ERROR"}',
            '{"level":" error ","request_id":"r-1"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, lines, "--request-id", "r-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "3\t" + lines[2] + "\n"
            "6\t" + lines[5] + "\n",
        )
        # JSON 损坏、缺 request_id 的行不产生额外警告；既有坏行警告保留行号。
        self.assertEqual(proc.stderr, "第 4 行：无效日志：JSON 解析失败\n")

    def test_without_request_id_keeps_level_only_results(self):
        import tempfile
        lines = [
            '{"level":"INFO","request_id":"r-1"}',
            '{"level":"ERROR","request_id":"r-2"}',
            '{"level":"ERROR","request_id":"r-1"}',
            "not-json",
            '{"level":"ERROR"}',
            '{"level":" error ","request_id":"r-1"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, lines)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "2\t" + lines[1] + "\n"
            "3\t" + lines[2] + "\n"
            "5\t" + lines[4] + "\n"
            "6\t" + lines[5] + "\n",
        )
        self.assertEqual(proc.stderr, "第 4 行：无效日志：JSON 解析失败\n")

    def test_exact_string_semantics(self):
        import tempfile
        # 大小写不同、首尾空白不同、子串、null、非字符串、缺失、同值在别的字段。
        lines = [
            '{"level":"ERROR","request_id":"R-1"}',
            '{"level":"ERROR","request_id":" r-1"}',
            '{"level":"ERROR","request_id":"r-12"}',
            '{"level":"ERROR","request_id":null}',
            '{"level":"ERROR","request_id":42}',
            '{"level":"ERROR"}',
            '{"level":"ERROR","other":"r-1"}',
            '{"level":"ERROR","request_id":"r-1"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, lines, "--request-id", "r-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "8\t" + lines[7] + "\n")
        # 不匹配的记录一律静默，不产生警告。
        self.assertEqual(proc.stderr, "")

    def test_whitespace_value_matched_exactly(self):
        import tempfile
        lines = [
            '{"level":"ERROR","request_id":" x "}',
            '{"level":"ERROR","request_id":"x"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, lines, "--request-id", " x ")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "1\t" + lines[0] + "\n")

    def test_missing_request_id_value(self):
        import tempfile
        lines = ['{"level":"ERROR","request_id":"r-1"}']
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, lines, "--request-id")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # argparse 错误说明中包含选项名 --request-id。
        self.assertIn("--request-id", proc.stderr)

    def test_empty_or_blank_request_id_value(self):
        import tempfile
        lines = ['{"level":"ERROR","request_id":"r-1"}']
        for value in ("", "   ", "\t"):
            with self.subTest(repr(value)):
                with tempfile.TemporaryDirectory() as d:
                    proc = self._run(d, lines, "--request-id", value)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("参数错误", proc.stderr)
                self.assertIn("--request-id", proc.stderr)


class SinceFilterTests(unittest.TestCase):
    """可选 --since：时间条件与既有条件同时满足才输出。"""

    SINCE_LINES = [
        '{"level":"ERROR","timestamp":"2026-10-03T09:59:59Z"}',
        '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z"}',
        '{"level":"ERROR","timestamp":"2026-10-03T10:00:01Z"}',
        '{"level":"ERROR","timestamp":null}',
    ]

    def _run(self, tmpdir, lines, *extra):
        path = Path(tmpdir) / "case.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return run_cli(str(path), "--level", "ERROR", *extra)

    def test_acceptance_example_with_since(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(
                d, self.SINCE_LINES, "--since", "2026-10-03T10:00:00Z"
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 含起点：第 1 行早于起点静默跳过，第 2、3 行输出，第 4 行时间警告。
        self.assertEqual(
            proc.stdout,
            "2\t" + self.SINCE_LINES[1] + "\n"
            "3\t" + self.SINCE_LINES[2] + "\n",
        )
        self.assertEqual(
            proc.stderr, "第 4 行：无效日志：timestamp 缺失或格式无效\n"
        )

    def test_without_since_no_timestamp_check(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, self.SINCE_LINES)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "".join(
                f"{i}\t{line}\n" for i, line in enumerate(self.SINCE_LINES, 1)
            ),
        )
        self.assertEqual(proc.stderr, "")

    def test_timestamp_checked_even_when_level_or_request_id_mismatch(self):
        import tempfile
        lines = [
            '{"level":"INFO","timestamp":null}',                    # 1 级别不匹配仍检查
            '{"level":"ERROR","request_id":"x","timestamp":"bad"}',  # 2 请求标识不匹配仍检查
            "not-json",                                              # 3 只有原有警告
            '["level","ERROR"]',                                     # 4 只有原有警告
            '{"level":"VERBOSE","timestamp":null}',                  # 5 只有原有警告
            '{"level":"ERROR","timestamp":"2026-10-03T09:00:00Z"}',  # 6 早于起点静默跳过
            '{"level":"ERROR","request_id":"y","timestamp":"2026-10-03T10:00:00Z"}',  # 7 输出
        ]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(
                d, lines, "--request-id", "y",
                "--since", "2026-10-03T10:00:00Z",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "7\t" + lines[6] + "\n")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：timestamp 缺失或格式无效\n"
            "第 2 行：无效日志：timestamp 缺失或格式无效\n"
            "第 3 行：无效日志：JSON 解析失败\n"
            "第 4 行：无效日志：顶层不是 JSON 对象\n"
            "第 5 行：无效日志：level 缺失或不属于支持的级别\n",
        )

    def test_invalid_timestamp_shapes_warn(self):
        import tempfile
        lines = [
            '{"level":"ERROR"}',                                        # 1 缺失
            '{"level":"ERROR","timestamp":null}',                       # 2 null
            '{"level":"ERROR","timestamp":42}',                         # 3 非字符串
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00.5Z"}',   # 4 小数秒
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00+08:00"}',# 5 时区偏移
            '{"level":"ERROR","timestamp":"2026-10-03t10:00:00z"}',     # 6 小写分隔
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:60Z"}',     # 7 闰秒
            '{"level":"ERROR","timestamp":" 2026-10-03T10:00:00Z"}',    # 8 首尾空白
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z"}',     # 9 合法，输出
        ]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, lines, "--since", "2026-10-03T10:00:00Z")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "9\t" + lines[8] + "\n")
        self.assertEqual(
            proc.stderr,
            "".join(
                f"第 {i} 行：无效日志：timestamp 缺失或格式无效\n"
                for i in range(1, 9)
            ),
        )

    def test_trailing_real_newline_in_timestamp_acceptance(self):
        import json
        import tempfile
        # 第一行 timestamp 中的 \n 是 JSON 转义，解码后为单个真实 LF；
        # 第二行 timestamp 合法。
        bad_line = json.dumps(
            {"level": "ERROR", "timestamp": "2026-10-03T10:00:00Z\n"},
            separators=(",", ":"),
        )
        good_line = json.dumps(
            {"level": "ERROR", "timestamp": "2026-10-03T10:00:00Z"},
            separators=(",", ":"),
        )
        lines = [bad_line, good_line]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # 启用 --since：仅第二行输出，警告仅指向第一行。
            proc_with = run_cli(
                str(path), "--level", "ERROR",
                "--since", "2026-10-03T10:00:00Z",
            )
            # 不启用 --since：两行都输出，完全不检查 timestamp。
            proc_without = run_cli(str(path), "--level", "ERROR")
        self.assertEqual(proc_with.returncode, 0, proc_with.stderr)
        self.assertEqual(proc_with.stdout, "2\t" + good_line + "\n")
        self.assertEqual(
            proc_with.stderr,
            "第 1 行：无效日志：timestamp 缺失或格式无效\n",
        )
        self.assertEqual(proc_without.returncode, 0, proc_without.stderr)
        self.assertEqual(
            proc_without.stdout,
            "1\t" + bad_line + "\n"
            "2\t" + good_line + "\n",
        )
        self.assertEqual(proc_without.stderr, "")

    def test_trailing_newline_timestamp_warns_even_when_filters_mismatch(self):
        import json
        import tempfile
        # 解码后带真实 LF 的时间戳，即使级别或请求标识不匹配也照常警告，
        # 每行只产生这一条时间警告；不匹配但时间戳合法的行静默跳过。
        lines = [
            json.dumps({"level": "INFO",
                        "timestamp": "2026-10-03T10:00:00Z\n"},
                       separators=(",", ":")),                    # 1 级别不匹配
            json.dumps({"level": "ERROR", "request_id": "x",
                        "timestamp": "2026-10-03T10:00:00Z\n"},
                       separators=(",", ":")),                    # 2 请求标识不匹配
            json.dumps({"level": "ERROR", "request_id": "x",
                        "timestamp": "2026-10-03T10:00:00Z"},
                       separators=(",", ":")),                    # 3 合法但不匹配，静默
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "case.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR", "--request-id", "y",
                "--since", "2026-10-03T10:00:00Z",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：timestamp 缺失或格式无效\n"
            "第 2 行：无效日志：timestamp 缺失或格式无效\n",
        )

    def test_invalid_since_value_rejected_before_reading_file(self):
        import tempfile
        bad_values = [
            "", "   ", "2026-10-03", "2026-10-03T10:00:00",
            "2026-10-03t10:00:00Z", "2026-10-03T10:00:00.5Z",
            "2026-10-03T10:00:00+00:00", "2026-10-03T10:00:60Z",
            "0000-01-01T00:00:00Z", "2026-02-30T00:00:00Z",
            "2026-10-03T24:00:00Z", " 2026-10-03T10:00:00Z",
            "2026-10-03T10:00:00Z\n",
        ]
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            for value in bad_values:
                with self.subTest(repr(value)):
                    proc = run_cli(
                        missing, "--level", "ERROR", "--since", value
                    )
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--since", proc.stderr)
                    # 不读取文件：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_since_value(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d, self.SINCE_LINES, "--since")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # argparse 错误说明中包含选项名 --since。
        self.assertIn("--since", proc.stderr)


class ModuleApiTests(unittest.TestCase):
    """模块公开函数的返回约定（直接调用，不经命令行）。"""

    def test_normalize_level(self):
        self.assertEqual(normalize_level(" eRrOr "), "ERROR")
        self.assertIsNone(normalize_level(42))
        self.assertIsNone(normalize_level(None))

    def test_iter_matches_returns_lineno_text_pairs(self):
        matches, warnings = iter_matches(CORE_LINES, "ERROR")
        self.assertEqual(matches, [(4, CORE_LINES[3]), (5, CORE_LINES[4])])
        self.assertEqual(warnings, [(3, "无效日志：JSON 解析失败")])

    def test_iter_matches_request_id_filter(self):
        lines = [
            '{"level":"ERROR","request_id":"a"}',
            '{"level":"ERROR","request_id":"b"}',
            "not-json",
            '{"level":"ERROR","request_id":null}',
        ]
        # 未传 request_id 时行为不变；传入时额外按精确字符串筛选。
        matches, _ = iter_matches(lines, "ERROR")
        self.assertEqual([lineno for lineno, _ in matches], [1, 2, 4])
        matches, warnings = iter_matches(lines, "ERROR", "a")
        self.assertEqual(matches, [(1, lines[0])])
        # 新筛选不掩盖既有坏行警告。
        self.assertEqual(warnings, [(3, "无效日志：JSON 解析失败")])

    def test_parse_timestamp(self):
        from datetime import datetime
        self.assertEqual(
            parse_timestamp("2026-10-03T10:00:00Z"),
            datetime(2026, 10, 3, 10, 0, 0),
        )
        for bad in (
            None, 42, "", "2026-10-03", "2026-10-03T10:00:00",
            "2026-10-03T10:00:00.0Z", "2026-10-03T10:00:00+00:00",
            "2026-10-03t10:00:00Z", "2026-10-03T10:00:60Z",
            "0000-01-01T00:00:00Z", "2026-02-30T00:00:00Z",
            " 2026-10-03T10:00:00Z",
            "2026-10-03T10:00:00Z\n", "2026-10-03T10:00:00Z\r\n",
            "\n2026-10-03T10:00:00Z",
        ):
            with self.subTest(repr(bad)):
                self.assertIsNone(parse_timestamp(bad))

    def test_iter_matches_since_filter(self):
        since = parse_timestamp("2026-10-03T10:00:00Z")
        lines = [
            '{"level":"ERROR","timestamp":"2026-10-03T09:59:59Z"}',
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z"}',
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:01Z"}',
            '{"level":"ERROR","timestamp":null}',
        ]
        # 不传 since 时行为与原来一致，不检查 timestamp。
        matches, warnings = iter_matches(lines, "ERROR")
        self.assertEqual([lineno for lineno, _ in matches], [1, 2, 3, 4])
        self.assertEqual(warnings, [])
        # 传入 since：含起点比较，非法 timestamp 产生警告。
        matches, warnings = iter_matches(lines, "ERROR", None, since)
        self.assertEqual(matches, [(2, lines[1]), (3, lines[2])])
        self.assertEqual(
            warnings, [(4, "无效日志：timestamp 缺失或格式无效")]
        )

    def test_iter_matches_since_trailing_newline_timestamp(self):
        since = parse_timestamp("2026-10-03T10:00:00Z")
        # JSON 转义的 \n 解码为真实 LF：时间格式非法，警告并跳过，不抛异常。
        lines = [
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z\\n"}',
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z"}',
        ]
        matches, warnings = iter_matches(lines, "ERROR", None, since)
        self.assertEqual(matches, [(2, lines[1])])
        self.assertEqual(
            warnings, [(1, "无效日志：timestamp 缺失或格式无效")]
        )


if __name__ == "__main__":
    unittest.main()
