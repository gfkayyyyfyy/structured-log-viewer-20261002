"""多级别相等筛选（重复 --level）的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中生成并在用例结束时清理，
不依赖 sample.jsonl、cr.jsonl 或外部服务。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from log_viewer import iter_matches

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 核心样例的七个物理行：
# 第 1 行有效但不匹配（INFO），第 2 行空白，第 3 行非法 JSON，
# 第 4 行匹配 ERROR（级别带首尾空白且小写），第 5 行匹配 WARNING，
# 第 6 行有效但不匹配（CRITICAL 不在所选级别内），第 7 行匹配 ERROR。
CORE_LINES = [
    '{"level":"INFO"}',
    "",
    "not-json",
    '{"level":" error ","message":"失败"}',
    '{"level":"WARNING"}',
    '{"level":"CRITICAL"}',
    '{"level":"ERROR","message":"末条"}',
]
# 筛选 " eRrOr " 与 WARNING 时：只输出第 4、5、7 行，
# 按原序保留行号、制表符分隔和完整原文；CRITICAL 不匹配。
CORE_STDOUT = (
    "4\t" + CORE_LINES[3] + "\n"
    "5\t" + CORE_LINES[4] + "\n"
    "7\t" + CORE_LINES[6] + "\n"
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


class MultiLevelFilterTests(unittest.TestCase):
    """重复 --level：命中任一所选级别即输出，行号、原序、原文不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "core.jsonl")
        Path(self.path).write_text(
            "\n".join(CORE_LINES) + "\n", encoding="utf-8"
        )

    def _assert_core_result(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, CORE_STDOUT)
        self.assertEqual(proc.stderr, CORE_STDERR)

    def test_two_levels_select_union(self):
        proc = run_cli(self.path, "--level", " eRrOr ", "--level", "WARNING")
        self._assert_core_result(proc)

    def test_option_order_does_not_matter(self):
        proc = run_cli(self.path, "--level", "WARNING", "--level", " eRrOr ")
        self._assert_core_result(proc)

    def test_duplicate_levels_do_not_change_result(self):
        for extra in (
            ("--level", "ERROR"),
            ("--level", " error "),
            ("--level", "WARNING"),
            ("--level", "warning"),
        ):
            with self.subTest(extra):
                proc = run_cli(
                    self.path,
                    "--level", " eRrOr ", "--level", "WARNING", *extra,
                )
                self._assert_core_result(proc)

    def test_line_endings_lf_crlf_and_no_final_newline(self):
        payloads = {
            "LF，末行有换行": ("\n".join(CORE_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(CORE_LINES) + "\r\n").encode("utf-8"),
            "末行无换行符": "\n".join(CORE_LINES).encode("utf-8"),
        }
        for index, (label, payload) in enumerate(payloads.items()):
            with self.subTest(label):
                path = Path(self._tmp.name) / f"core-{index}.jsonl"
                path.write_bytes(payload)
                proc = run_cli(
                    str(path), "--level", " eRrOr ", "--level", "WARNING"
                )
                self._assert_core_result(proc)

    def test_level_without_matches_keeps_warnings(self):
        # DEBUG 在样例中没有匹配记录：标准输出为空，
        # 原有坏行警告仍保留，退出码为 0。
        proc = run_cli(self.path, "--level", "DEBUG")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, CORE_STDERR)


class MultiLevelArgumentErrorTests(unittest.TestCase):
    """--level 多次出现时的参数边界：任一非法值即整体拒绝。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")

    def test_invalid_value_rejected_regardless_of_position(self):
        bad_values = ("", "   ", "\t", "TRACE", "trace")
        for bad in bad_values:
            for position, levels in (
                ("非法值在前", (bad, "ERROR")),
                ("非法值在后", ("ERROR", bad)),
                ("合法值夹非法值", ("WARNING", bad, "ERROR")),
            ):
                with self.subTest(value=repr(bad), position=position):
                    args = [self.missing]
                    for level in levels:
                        args += ["--level", level]
                    proc = run_cli(*args)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--level", proc.stderr)
                    # 参数校验在读取文件之前：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_level_value(self):
        # 某次 --level 缺值：argparse 拒绝，退出码 2，标准输出为空。
        for args in (
            (self.missing, "--level"),
            (self.missing, "--level", "ERROR", "--level"),
        ):
            with self.subTest(args):
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("--level", proc.stderr)
                self.assertNotIn("文件读取失败", proc.stderr)


class MultiLevelModuleApiTests(unittest.TestCase):
    """iter_matches 直接调用：级别序列与单级别字符串的返回约定。"""

    def test_sequence_of_levels_selects_union_in_file_order(self):
        # 列表与元组均可；匹配始终按文件行序，与序列内顺序无关。
        expected_matches = [
            (4, CORE_LINES[3]),
            (5, CORE_LINES[4]),
            (7, CORE_LINES[6]),
        ]
        expected_warnings = [(3, "无效日志：JSON 解析失败")]
        for levels in (
            ["ERROR", "WARNING"],
            ("ERROR", "WARNING"),
            ["WARNING", "ERROR"],
            ("WARNING", "ERROR"),
        ):
            with self.subTest(levels):
                matches, warnings = iter_matches(CORE_LINES, levels)
                self.assertEqual(matches, expected_matches)
                self.assertEqual(warnings, expected_warnings)
                # 返回值仍是行号和文本的列表。
                self.assertIsInstance(matches, list)
                self.assertIsInstance(warnings, list)

    def test_duplicate_levels_in_sequence_do_not_duplicate_output(self):
        matches, warnings = iter_matches(
            CORE_LINES, ["WARNING", "ERROR", "ERROR", "WARNING"]
        )
        self.assertEqual(
            matches,
            [(4, CORE_LINES[3]), (5, CORE_LINES[4]), (7, CORE_LINES[6])],
        )
        self.assertEqual(warnings, [(3, "无效日志：JSON 解析失败")])

    def test_single_string_matches_single_element_sequence(self):
        # 单个 ERROR 字符串与只含 ERROR 的序列结果完全相同。
        expected_matches = [(4, CORE_LINES[3]), (7, CORE_LINES[6])]
        expected_warnings = [(3, "无效日志：JSON 解析失败")]
        results = [
            iter_matches(CORE_LINES, "ERROR"),
            iter_matches(CORE_LINES, ["ERROR"]),
            iter_matches(CORE_LINES, ("ERROR",)),
        ]
        for matches, warnings in results:
            self.assertEqual(matches, expected_matches)
            self.assertEqual(warnings, expected_warnings)


if __name__ == "__main__":
    unittest.main()
