"""多级别相等筛选（--level 重复出现）的离线回归测试。

命令行已支持重复提供 --level，iter_matches 也接受单个级别字符串或
已规范化的级别序列；本模块为这组多级别筛选补齐回归测试，不覆盖
请求标识、时间筛选与文件读取失败（它们继续沿用既有测试协议）。

只用 Python 3 标准库（unittest / subprocess / tempfile / io / contextlib），
测试输入均在用例自建的临时目录中生成并自动清理，不依赖 sample.jsonl、
网络或外部服务。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from log_viewer import iter_matches
from log_viewer.__main__ import main

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 多级别筛选的核心七行样例（按物理行顺序）：
# 第 1 行有效但不匹配；第 2 行空白；第 3 行非法 JSON；
# 第 4 行级别为带首尾空白的小写 error（规范化后命中 ERROR）；
# 第 5 行 WARNING；第 6 行 CRITICAL（不在所选集合中，不匹配）；
# 第 7 行 ERROR。
MULTI_LINES = [
    '{"level":"INFO"}',
    "",
    "not-json",
    '{"level":" error ","message":"失败"}',
    '{"level":"WARNING"}',
    '{"level":"CRITICAL"}',
    '{"level":"ERROR","message":"末条"}',
]

# --level " eRrOr " --level WARNING 时：标准输出只含第 4、5、7 行，
# 原始行号 + 制表符 + 完整原文，顺序不变；CRITICAL（第 6 行）不匹配。
MULTI_STDOUT = (
    "4\t" + MULTI_LINES[3] + "\n"
    "5\t" + MULTI_LINES[4] + "\n"
    "7\t" + MULTI_LINES[6] + "\n"
)
# 标准错误只有第 3 行这一条 JSON 解析失败警告。
MULTI_STDERR = "第 3 行：无效日志：JSON 解析失败\n"

# 直接调用 iter_matches 时多级别筛选的期望返回值：
# matches 与 warnings 均为 (行号, 文本) 列表。
MULTI_MATCHES = [
    (4, MULTI_LINES[3]),
    (5, MULTI_LINES[4]),
    (7, MULTI_LINES[6]),
]
MULTI_WARNINGS = [(3, "无效日志：JSON 解析失败")]


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


def run_main(argv):
    """直接调用 main(argv)，返回 (返回值, 标准输出, 标准错误)。

    用于核对模块入口的返回值（不经子进程与退出码转换）。
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = main(argv)
    return result, stdout.getvalue(), stderr.getvalue()


class MultiLevelCliTests(unittest.TestCase):
    """重复 --level：并集匹配，顺序、重复值、大小写空白均有固定结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, content: bytes, name="multi.jsonl") -> str:
        path = Path(self._tmp.name) / name
        path.write_bytes(content)
        return str(path)

    def test_error_and_warning_union_keeps_lineno_and_verbatim_text(self):
        # LF、CRLF、末行无终止换行符三种写法下行号与正文完全一致。
        payloads = {
            "LF，末行有换行": ("\n".join(MULTI_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(MULTI_LINES) + "\r\n").encode("utf-8"),
            "末行无换行符": "\n".join(MULTI_LINES).encode("utf-8"),
        }
        for label, payload in payloads.items():
            with self.subTest(label):
                path = self._write(payload, name=f"case-{label}.jsonl")
                proc = run_cli(
                    path, "--level", " eRrOr ", "--level", "WARNING"
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, MULTI_STDOUT)
                self.assertEqual(proc.stderr, MULTI_STDERR)

    def test_option_order_duplicates_do_not_change_result(self):
        path = self._write(("\n".join(MULTI_LINES) + "\n").encode("utf-8"))
        # 交换两个选项；追加重复的 ERROR；追加重复的 WARNING：
        # 结果与基础样例完全相同，CRITICAL 始终不匹配。
        invocations = [
            [path, "--level", " eRrOr ", "--level", "WARNING"],
            [path, "--level", "WARNING", "--level", " eRrOr "],
            [
                path, "--level", " eRrOr ", "--level", "WARNING",
                "--level", "ERROR",
            ],
            [
                path, "--level", "ERROR", "--level", "WARNING",
                "--level", " error ",
            ],
            [
                path, "--level", " eRrOr ", "--level", "WARNING",
                "--level", "warning",
            ],
            [
                path, "--level", "WARNING", "--level", "WARNING",
                "--level", " eRrOr ",
            ],
        ]
        for argv in invocations:
            with self.subTest(argv[1:]):
                proc = run_cli(*argv)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, MULTI_STDOUT)
                self.assertEqual(proc.stderr, MULTI_STDERR)
                # CRITICAL（第 6 行）在任何写法下都不出现在输出中。
                self.assertNotIn("6\t", proc.stdout)

    def test_debug_only_matches_nothing_but_keeps_bad_line_warning(self):
        path = self._write(("\n".join(MULTI_LINES) + "\n").encode("utf-8"))
        proc = run_cli(path, "--level", "DEBUG")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        # 只选择没有匹配记录的级别时，原有坏行警告仍保留。
        self.assertEqual(proc.stderr, MULTI_STDERR)

    def test_main_return_value_matches_process_contract(self):
        # 不经子进程直接调用 main：返回值为 0，两个流的内容与 CLI 相同。
        path = self._write(("\n".join(MULTI_LINES) + "\n").encode("utf-8"))
        result, stdout, stderr = run_main(
            [path, "--level", " eRrOr ", "--level", "WARNING"]
        )
        self.assertEqual(result, 0)
        self.assertEqual(stdout, MULTI_STDOUT)
        self.assertEqual(stderr, MULTI_STDERR)


class IterMatchesMultiLevelTests(unittest.TestCase):
    """iter_matches 的级别序列入参：规范化列表/元组、并集、重复值、原序。"""

    def test_normalized_list_and_tuple_give_same_union(self):
        expected_matches = MULTI_MATCHES
        expected_warnings = MULTI_WARNINGS
        # 规范化的列表与元组结果一致。
        for levels in (
            ["ERROR", "WARNING"],
            ("ERROR", "WARNING"),
            ["WARNING", "ERROR"],
            ("WARNING", "ERROR"),
        ):
            with self.subTest(repr(levels)):
                matches, warnings = iter_matches(MULTI_LINES, levels)
                # 命中行始终按文件原序排列，与序列中的级别顺序无关。
                self.assertEqual(matches, expected_matches)
                self.assertEqual(warnings, expected_warnings)
                # 返回的匹配与警告仍是行号和文本的列表。
                self.assertIsInstance(matches, list)
                self.assertIsInstance(warnings, list)
                self.assertTrue(
                    all(isinstance(item, tuple) and len(item) == 2
                        for item in matches + warnings)
                )

    def test_duplicate_levels_do_not_duplicate_output(self):
        # 重复值不增加输出，行序不变。
        for levels in (
            ["ERROR", "WARNING", "ERROR"],
            ("ERROR", "WARNING", "WARNING"),
            ["WARNING", "ERROR", "WARNING", "ERROR", "ERROR"],
        ):
            with self.subTest(repr(levels)):
                matches, warnings = iter_matches(MULTI_LINES, levels)
                self.assertEqual(matches, MULTI_MATCHES)
                self.assertEqual(warnings, MULTI_WARNINGS)
                linenos = [lineno for lineno, _ in matches]
                self.assertEqual(linenos, sorted(linenos))
                self.assertEqual(len(linenos), len(set(linenos)))

    def test_single_level_string_equals_single_element_sequence(self):
        # 单个 ERROR 字符串与只含 ERROR 的序列得到相同结果。
        from_string = iter_matches(MULTI_LINES, "ERROR")
        from_list = iter_matches(MULTI_LINES, ["ERROR"])
        from_tuple = iter_matches(MULTI_LINES, ("ERROR",))
        self.assertEqual(from_string, from_list)
        self.assertEqual(from_string, from_tuple)
        # 只有第 4、7 行命中 ERROR；WARNING 第 5 行不在其中。
        matches, warnings = from_string
        self.assertEqual(
            matches,
            [(4, MULTI_LINES[3]), (7, MULTI_LINES[6])],
        )
        self.assertEqual(warnings, MULTI_WARNINGS)

    def test_main_return_value_on_argument_error(self):
        # 参数非法时 main 直接返回 2，标准输出为空，标准错误指出参数问题。
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            result, stdout, stderr = run_main(
                [missing, "--level", "ERROR", "--level", "TRACE"]
            )
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("参数错误", stderr)
        self.assertIn("--level", stderr)
        self.assertNotIn("文件读取失败", stderr)


class MultiLevelArgumentBoundaryTests(unittest.TestCase):
    """参数边界聚焦 --level 的多次出现：任一值非法即退出 2，且先于读文件。"""

    def test_empty_blank_or_unsupported_value_rejected_before_reading_file(self):
        # 空字符串、全空白、不支持的 TRACE，不论出现在合法值之前还是之后。
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            invalid_level_argv = [
                # 非法值是唯一的值。
                [missing, "--level", ""],
                [missing, "--level", "   "],
                [missing, "--level", "\t"],
                [missing, "--level", "TRACE"],
                # 非法值在合法值之后。
                [missing, "--level", "ERROR", "--level", ""],
                [missing, "--level", "ERROR", "--level", "  "],
                [missing, "--level", "ERROR", "--level", "TRACE"],
                [missing, "--level", "WARNING", "--level", "trace"],
                # 非法值在合法值之前。
                [missing, "--level", "", "--level", "ERROR"],
                [missing, "--level", "  ", "--level", "ERROR"],
                [missing, "--level", "TRACE", "--level", "ERROR"],
            ]
            for argv in invalid_level_argv:
                with self.subTest(argv[1:]):
                    proc = run_cli(*argv)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--level", proc.stderr)
                    # 所有参数校验都在读文件之前：不存在的路径不产生读取失败信息。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_level_value(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            path.write_text("\n".join(MULTI_LINES) + "\n", encoding="utf-8")
            # --level 位于参数末尾、缺值；以及其后紧跟另一个选项时缺值。
            for argv in (
                [str(path), "--level"],
                [str(path), "--level", "WARNING", "--level"],
            ):
                with self.subTest(argv[1:]):
                    proc = run_cli(*argv)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    # argparse 的错误说明中包含选项名 --level。
                    self.assertIn("--level", proc.stderr)


if __name__ == "__main__":
    unittest.main()
