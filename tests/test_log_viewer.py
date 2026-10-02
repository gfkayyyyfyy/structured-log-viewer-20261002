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

from log_viewer import iter_matches, normalize_level

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


if __name__ == "__main__":
    unittest.main()
