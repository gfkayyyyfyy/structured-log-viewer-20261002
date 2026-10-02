"""log_viewer 命令行流程（python -m log_viewer）的离线回归测试。

在项目根目录执行：

    python -m unittest discover -s tests

全部通过时退出码为 0；断言失败时退出码非零并指出失败用例。

测试输入均在临时目录中现场生成，不依赖 sample.jsonl，也不访问网络。
仅使用 Python 3 标准库。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 核心示例的五个物理行（不含行尾换行符）：
# 有效 INFO、空白行、非法 JSON、带首尾空白的 error、标准 ERROR
CORE_LINES = [
    '{"level":"INFO"}',
    '',
    'not-json',
    '{"level":" error ","message":"失败"}',
    '{"level":"ERROR","message":"第二条"}',
]
CORE_STDOUT = (
    '4\t{"level":" error ","message":"失败"}\n'
    '5\t{"level":"ERROR","message":"第二条"}\n'
)
CORE_STDERR = '第 3 行：无效日志：JSON 解析失败\n'


class LogViewerCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_bytes(self, name, data):
        """以二进制写临时文件，避免平台换行符转换干扰 LF/CRLF 用例。"""
        path = self.tmpdir / name
        path.write_bytes(data)
        return path

    def run_cli(self, *args):
        """以子进程方式运行 python -m log_viewer，返回 CompletedProcess。"""
        env = os.environ.copy()
        # 固定标准流编码，保证中文输出在任何区域设置下均可按 UTF-8 断言
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "log_viewer", *args],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def filter_level(self, path, level):
        return self.run_cli(str(path), "--level", level)

    # --level " eRrOr " 筛选核心示例：仅第 4、5 行输出，第 3 行一条警告 ----

    def test_core_example_lf_crlf_and_without_final_newline(self):
        variants = {
            "LF": ("\n".join(CORE_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(CORE_LINES) + "\r\n").encode("utf-8"),
            "no-final-newline": "\n".join(CORE_LINES).encode("utf-8"),
        }
        for label, data in variants.items():
            with self.subTest(line_ending=label):
                path = self.write_bytes(f"core-{label}.jsonl", data)
                proc = self.filter_level(path, " eRrOr ")
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout.decode("utf-8"), CORE_STDOUT)
                self.assertEqual(proc.stderr.decode("utf-8"), CORE_STDERR)

    # 匹配行的首尾空格与中文内容原样保留（CRLF 下也不吞掉空格）----------

    def test_matching_lines_keep_surrounding_spaces_and_chinese(self):
        lines = [
            '  {"level":"ERROR","message":"保留"}  ',
            '{"level":"INFO","message":"不匹配"}',
            '{"level":"ERROR","message":"中文内容：失败原因（代码 42）"}',
        ]
        expected_stdout = (
            '1\t  {"level":"ERROR","message":"保留"}  \n'
            '3\t{"level":"ERROR","message":"中文内容：失败原因（代码 42）"}\n'
        )
        for label, separator in (("LF", "\n"), ("CRLF", "\r\n")):
            with self.subTest(line_ending=label):
                data = (separator.join(lines) + separator).encode("utf-8")
                path = self.write_bytes(f"spaces-{label}.jsonl", data)
                proc = self.filter_level(path, "ERROR")
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout.decode("utf-8"), expected_stdout)
                self.assertEqual(proc.stderr.decode("utf-8"), "")

    # 各类无效非空行各产生一条带正确行号的警告，后续有效行仍输出 ----------

    def test_each_invalid_nonempty_line_warns_once_with_line_number(self):
        lines = [
            '{"level":"INFO"}',                          # 1 有效但不匹配
            '[1, 2, 3]',                                 # 2 顶层数组
            '{"message":"缺少 level"}',                   # 3 缺少 level
            '{"level":123}',                             # 4 非字符串 level
            '{"level":"TRACE"}',                         # 5 不支持的级别
            '{"level":"ERROR","message":"最后仍输出"}',    # 6 有效且匹配
        ]
        path = self.write_bytes("invalid-kinds.jsonl",
                                ("\n".join(lines) + "\n").encode("utf-8"))
        proc = self.filter_level(path, "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout.decode("utf-8"),
            '6\t{"level":"ERROR","message":"最后仍输出"}\n',
        )
        self.assertEqual(
            proc.stderr.decode("utf-8"),
            "第 2 行：无效日志：顶层不是 JSON 对象\n"
            "第 3 行：无效日志：level 缺失或不属于支持的级别\n"
            "第 4 行：无效日志：level 缺失或不属于支持的级别\n"
            "第 5 行：无效日志：level 缺失或不属于支持的级别\n",
        )

    # 五个级别各自的相等匹配语义 ------------------------------------------

    def test_all_five_levels_match_by_equality_only(self):
        levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        lines = [f'{{"level":"{level}"}}' for level in levels]
        data = ("\n".join(lines) + "\n").encode("utf-8")
        path = self.write_bytes("levels.jsonl", data)
        for index, level in enumerate(levels, start=1):
            with self.subTest(level=level):
                # 同时固定 CLI 端忽略大小写与首尾空白的行为
                proc = self.filter_level(path, f" {level.lower()} ")
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(
                    proc.stdout.decode("utf-8"),
                    f"{index}\t{{\"level\":\"{level}\"}}\n",
                )
                self.assertEqual(proc.stderr.decode("utf-8"), "")

    # 结束结果：无任何输出且退出码 0 的若干情形 ----------------------------

    def test_empty_file_outputs_nothing(self):
        path = self.write_bytes("empty.jsonl", b"")
        proc = self.filter_level(path, "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def test_only_blank_lines_outputs_nothing(self):
        path = self.write_bytes("blanks.jsonl", b"\n  \n\t\n")
        proc = self.filter_level(path, "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def test_all_valid_records_non_matching_outputs_nothing(self):
        data = b'{"level":"DEBUG"}\n{"level":"INFO"}\n'
        path = self.write_bytes("no-match.jsonl", data)
        proc = self.filter_level(path, "CRITICAL")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def test_only_invalid_lines_stdout_empty_warnings_sorted_by_line_number(self):
        lines = ["not-json", '{"level":"NOPE"}', "[1]"]
        path = self.write_bytes("only-invalid.jsonl",
                                ("\n".join(lines) + "\n").encode("utf-8"))
        proc = self.filter_level(path, "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr.decode("utf-8"),
            "第 1 行：无效日志：JSON 解析失败\n"
            "第 2 行：无效日志：level 缺失或不属于支持的级别\n"
            "第 3 行：无效日志：顶层不是 JSON 对象\n",
        )

    # 参数错误：stdout 为空、stderr 含参数错误信息、退出码 2 ---------------

    def test_missing_level_argument_exits_2(self):
        path = self.write_bytes("arg.jsonl", b'{"level":"INFO"}\n')
        proc = self.run_cli(str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("--level", proc.stderr.decode("utf-8", "replace"))

    def test_unsupported_level_argument_exits_2(self):
        path = self.write_bytes("arg.jsonl", b'{"level":"INFO"}\n')
        proc = self.filter_level(path, "TRACE")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("参数错误", proc.stderr.decode("utf-8"))

    # 文件读取失败：stdout 为空、stderr 含失败信息、退出码 2 ---------------
    # 只断言稳定的中文前缀，不固定操作系统附加的错误细节 ------------------

    def test_nonexistent_path_exits_2(self):
        proc = self.filter_level(self.tmpdir / "does-not-exist.jsonl", "ERROR")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("文件读取失败", proc.stderr.decode("utf-8", "replace"))

    def test_directory_path_exits_2(self):
        proc = self.filter_level(self.tmpdir, "ERROR")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("文件读取失败", proc.stderr.decode("utf-8", "replace"))

    def test_invalid_utf8_bytes_exits_2(self):
        path = self.write_bytes("bad-utf8.jsonl", b"\xff\xfe\x00bad\n")
        proc = self.filter_level(path, "ERROR")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("文件读取失败", proc.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
