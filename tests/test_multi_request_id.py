"""重复 --request-id（多请求标识并集）的离线回归测试。

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

# 核心样例的八个物理行（非空白有效行均为只含 level 和 request_id 的对象）：
# 第 1 行标识 r-2；第 2 行空白；第 3 行正文 not-json；
# 第 4、5 行标识 r-1（第 4 行级别为首尾各带一个空格的小写 error，
# 第 5 行级别为 INFO）；第 6 行标识 R-1；第 7 行标识为 " r-1 "
# （首尾各带一个空格）；第 8 行标识 r-12。除第 4、5 行外级别均为 ERROR。
CORE_LINES = [
    '{"level":"ERROR","request_id":"r-2"}',
    "",
    "not-json",
    '{"level":" error ","request_id":"r-1"}',
    '{"level":"INFO","request_id":"r-1"}',
    '{"level":"ERROR","request_id":"R-1"}',
    '{"level":"ERROR","request_id":" r-1 "}',
    '{"level":"ERROR","request_id":"r-12"}',
]
# 选择 ERROR 并重复指定 r-1、r-2 时：标识集合先取并集，再与级别条件取
# 交集——只输出第 1 行（r-2）和第 4 行（r-1，级别经规范化为 ERROR）。
# 第 5 行级别为 INFO；第 6 行大小写不同；第 7 行首尾空格不同；
# 第 8 行 r-12 只是 r-1 的超串：均不匹配。
CORE_STDOUT = (
    "1\t" + CORE_LINES[0] + "\n"
    "4\t" + CORE_LINES[3] + "\n"
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


class RepeatedRequestIdFilterTests(unittest.TestCase):
    """重复 --request-id：标识取并集后与级别条件取交集，行号、原序、原文不变。"""

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

    def test_two_request_ids_select_union(self):
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--request-id", "r-1", "--request-id", "r-2",
        )
        self._assert_core_result(proc)

    def test_option_order_does_not_matter(self):
        # 交换两个标识的给出顺序：并集不变，输出仍按文件行序。
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--request-id", "r-2", "--request-id", "r-1",
        )
        self._assert_core_result(proc)

    def test_duplicate_request_ids_do_not_change_result(self):
        # 再次指定已有标识（含大小写、首尾空白不同的等价写法）不增加输出。
        for extra in (
            ("--request-id", "r-1"),
            ("--request-id", "r-2"),
            ("--request-id", "r-1", "--request-id", "r-2"),
        ):
            with self.subTest(extra):
                proc = run_cli(
                    self.path, "--level", "ERROR",
                    "--request-id", "r-1", "--request-id", "r-2", *extra,
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
                    str(path), "--level", "ERROR",
                    "--request-id", "r-1", "--request-id", "r-2",
                )
                self._assert_core_result(proc)

    def test_whitespace_value_is_not_trimmed(self):
        # 合法标识首尾各带一个空格时原样参与比较：只匹配第 7 行，
        # 第 4 行的 r-1 不匹配，参数本身也不因首尾空白被拒绝。
        proc = run_cli(
            self.path, "--level", "ERROR", "--request-id", " r-1 ",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "7\t" + CORE_LINES[6] + "\n")
        # 标识不匹配的行静默跳过；第 3 行既有的 JSON 警告不受筛选影响。
        self.assertEqual(proc.stderr, CORE_STDERR)

    def test_case_sensitive_and_substring_ids_do_not_match(self):
        # R-1 只命中第 6 行；r-12 只命中第 8 行（r-1 不被当作前缀）；
        # 同时给 r-1 与 R-1 时各行其是，不匹配记录一律静默。
        cases = [
            (["R-1"], [(6, CORE_LINES[5])]),
            (["r-12"], [(8, CORE_LINES[7])]),
            (["r-1", "R-1"], [(4, CORE_LINES[3]), (6, CORE_LINES[5])]),
        ]
        for values, expected in cases:
            with self.subTest(values):
                args = [self.path, "--level", "ERROR"]
                for value in values:
                    args += ["--request-id", value]
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(
                    proc.stdout,
                    "".join(f"{lineno}\t{text}\n" for lineno, text in expected),
                )
                # 不匹配的记录一律静默；第 3 行既有的 JSON 警告仍保留。
                self.assertEqual(proc.stderr, CORE_STDERR)

    def test_missing_null_number_and_nested_ids_are_silently_skipped(self):
        lines = [
            '{"level":"ERROR","request_id":null}',
            '{"level":"ERROR","request_id":42}',
            '{"level":"ERROR"}',
            '{"level":"ERROR","nested":{"request_id":"r-1"}}',
            '{"level":"ERROR","request_id":"r-1"}',
        ]
        path = Path(self._tmp.name) / "shapes.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--request-id", "r-1", "--request-id", "r-2",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 缺失、null、数字以及只出现在嵌套对象中均静默跳过，不产生警告。
        self.assertEqual(proc.stdout, "5\t" + lines[4] + "\n")
        self.assertEqual(proc.stderr, "")


class RepeatedRequestIdArgumentErrorTests(unittest.TestCase):
    """--request-id 多次出现时的参数边界：任一空值即整体拒绝，且先于读文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用确定不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")
        self.assertFalse(Path(self.missing).exists())

    def _run_with_ids(self, request_ids):
        args = [self.missing, "--level", "ERROR"]
        for request_id in request_ids:
            args += ["--request-id", request_id]
        return run_cli(*args)

    def test_empty_or_blank_value_rejected_regardless_of_position(self):
        blank_values = ("", "   ", "\t")
        for blank in blank_values:
            for position, request_ids in (
                ("空值在合法值之前", (blank, "r-1")),
                ("空值在合法值之后", ("r-1", blank)),
                ("合法值夹空值", ("r-2", blank, "r-1")),
                ("全部为空或全空白", ("", "   ")),
            ):
                with self.subTest(value=repr(blank), position=position):
                    proc = self._run_with_ids(request_ids)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--request-id", proc.stderr)
                    # 参数校验在读取文件之前：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_request_id_value(self):
        # 某次 --request-id 缺值（无论在末尾还是中间）：argparse 拒绝，
        # 退出码 2，标准输出为空，错误说明指出该选项。
        for args in (
            (self.missing, "--level", "ERROR", "--request-id"),
            (
                self.missing, "--level", "ERROR",
                "--request-id", "r-1", "--request-id",
            ),
            (
                self.missing, "--level", "ERROR",
                "--request-id", "r-1", "--request-id", "--request-id", "r-2",
            ),
        ):
            with self.subTest(args):
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("--request-id", proc.stderr)
                self.assertNotIn("文件读取失败", proc.stderr)


class RepeatedRequestIdModuleApiTests(unittest.TestCase):
    """iter_matches 直接调用：标识序列与单标识字符串的返回约定。"""

    EXPECTED_MATCHES = [(1, CORE_LINES[0]), (4, CORE_LINES[3])]
    EXPECTED_WARNINGS = [(3, "无效日志：JSON 解析失败")]

    def test_list_and_tuple_select_union_in_file_order(self):
        # 列表与元组均可；匹配始终按文件行序，与序列内顺序、重复标识无关。
        for request_ids in (
            ["r-1", "r-2"],
            ("r-1", "r-2"),
            ["r-2", "r-1"],
            ("r-2", "r-1"),
            ["r-1", "r-2", "r-1", "r-2"],
        ):
            with self.subTest(request_ids):
                matches, warnings = iter_matches(
                    CORE_LINES, "ERROR", request_ids
                )
                # 匹配列表与警告列表均保留原行号和原文。
                self.assertEqual(matches, self.EXPECTED_MATCHES)
                self.assertEqual(warnings, self.EXPECTED_WARNINGS)
                self.assertIsInstance(matches, list)
                self.assertIsInstance(warnings, list)

    def test_single_string_matches_single_element_sequence(self):
        # 单个 "r-2" 字符串与只含 "r-2" 的列表、元组结果完全相同：只命中第 1 行。
        results = [
            iter_matches(CORE_LINES, "ERROR", "r-2"),
            iter_matches(CORE_LINES, "ERROR", ["r-2"]),
            iter_matches(CORE_LINES, "ERROR", ("r-2",)),
        ]
        for matches, warnings in results:
            self.assertEqual(matches, [(1, CORE_LINES[0])])
            self.assertEqual(warnings, self.EXPECTED_WARNINGS)

    def test_whitespace_value_is_not_trimmed(self):
        # 模块层同样原样比较：" r-1 " 只命中第 7 行，不命中 r-1。
        for request_ids in ([" r-1 "], (" r-1 ",)):
            with self.subTest(request_ids):
                matches, warnings = iter_matches(
                    CORE_LINES, "ERROR", request_ids
                )
                self.assertEqual(matches, [(7, CORE_LINES[6])])
                self.assertEqual(warnings, self.EXPECTED_WARNINGS)


if __name__ == "__main__":
    unittest.main()
