"""重复 --request-id（请求标识并集）的离线回归测试。

验证命令行已支持的既有行为：重复提供 --request-id 时，多个标识取并集，
再与级别条件取交集；iter_matches 同样接受请求标识序列。

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

# 核心样例的八个物理行（非空白且有效的行均为只含 level 和 request_id 的
# JSON 对象）：
# 第 1 行 r-2 / ERROR；第 2 行空白；第 3 行非法 JSON；
# 第 4 行 r-1，级别为首尾各带一个空格的小写 error（规范化后为 ERROR）；
# 第 5 行 r-1 / INFO（级别不匹配）；第 6 行 R-1（大小写不同）；
# 第 7 行标识首尾各带一个空格（值不被裁剪）；第 8 行 r-12（仅为子串）。
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
# 选择 ERROR 并重复指定 r-1、r-2 时：标识并集 {r-1, r-2} 与 ERROR 级别
# 取交集，只输出第 1、4 行，按原序保留行号、制表符分隔和完整原文。
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
    """重复 --request-id：标识取并集后与级别条件取交集，行号原文不变。"""

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

    def test_two_request_ids_select_union_intersected_with_level(self):
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--request-id", "r-1", "--request-id", "r-2",
        )
        self._assert_core_result(proc)

    def test_option_order_does_not_matter(self):
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--request-id", "r-2", "--request-id", "r-1",
        )
        self._assert_core_result(proc)

    def test_duplicate_request_ids_do_not_change_result(self):
        repetitions = (
            ("--request-id", "r-1", "--request-id", "r-1",
             "--request-id", "r-2"),
            ("--request-id", "r-2", "--request-id", "r-2",
             "--request-id", "r-1"),
            ("--request-id", "r-1", "--request-id", "r-2",
             "--request-id", "r-1", "--request-id", "r-2"),
        )
        for extra in repetitions:
            with self.subTest(extra):
                proc = run_cli(
                    self.path, "--level", "ERROR", *extra
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
        # 合法标识首尾带空格时原样参与比较：只命中第 7 行，
        # 第 4 行的 r-1 不会因为裁剪而误匹配。
        proc = run_cli(
            self.path, "--level", "ERROR", "--request-id", " r-1 "
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "7\t" + CORE_LINES[6] + "\n")
        self.assertEqual(proc.stderr, CORE_STDERR)

    def test_case_difference_and_substring_do_not_match(self):
        # 只给 r-1：第 4 行输出；第 5 行级别为 INFO，第 6 行 R-1 大小写
        # 不同，第 7 行带空格，第 8 行 r-12 仅为子串，均不输出。
        proc = run_cli(
            self.path, "--level", "ERROR", "--request-id", "r-1"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "4\t" + CORE_LINES[3] + "\n")
        self.assertEqual(proc.stderr, CORE_STDERR)

    def test_missing_null_number_and_nested_ids_silently_skipped(self):
        # 顶层标识缺失、为 null、为数字或只在嵌套对象中出现：
        # 与标识集合不相等即静默跳过，不产生警告。
        lines = [
            '{"level":"ERROR"}',
            '{"level":"ERROR","request_id":null}',
            '{"level":"ERROR","request_id":42}',
            '{"level":"ERROR","ctx":{"request_id":"r-1"}}',
            '{"level":" error ","request_id":"r-1"}',
        ]
        path = Path(self._tmp.name) / "shapes.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--request-id", "r-1", "--request-id", "r-2",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "5\t" + lines[4] + "\n")
        self.assertEqual(proc.stderr, "")


class RepeatedRequestIdArgumentErrorTests(unittest.TestCase):
    """--request-id 多次出现时的参数边界：任一空值即整体拒绝，先于读文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用确定不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")

    def test_empty_or_blank_value_rejected_regardless_of_position(self):
        bad_values = ("", "   ", "\t")
        for bad in bad_values:
            for position, request_ids in (
                ("空值在合法值之前", (bad, "r-1")),
                ("空值在合法值之后", ("r-1", bad)),
                ("合法值夹住空值", ("r-2", bad, "r-1")),
                ("全部为空或全空白", (bad, "  ")),
            ):
                with self.subTest(value=repr(bad), position=position):
                    args = [self.missing, "--level", "ERROR"]
                    for request_id in request_ids:
                        args += ["--request-id", request_id]
                    proc = run_cli(*args)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--request-id", proc.stderr)
                    # 参数校验在读取文件之前：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_request_id_value(self):
        # 某次 --request-id 缺值：argparse 拒绝，退出码 2，标准输出为空，
        # 错误信息指出该选项；无论缺值出现在第一次还是后续某次。
        for args in (
            (self.missing, "--level", "ERROR", "--request-id"),
            (self.missing, "--level", "ERROR",
             "--request-id", "r-1", "--request-id"),
        ):
            with self.subTest(args):
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("--request-id", proc.stderr)
                self.assertNotIn("文件读取失败", proc.stderr)


class RepeatedRequestIdModuleApiTests(unittest.TestCase):
    """iter_matches 直接调用：请求标识序列与单标识字符串的返回约定。"""

    EXPECTED_MATCHES = [
        (1, CORE_LINES[0]),
        (4, CORE_LINES[3]),
    ]
    EXPECTED_WARNINGS = [(3, "无效日志：JSON 解析失败")]

    def test_list_and_tuple_select_same_union(self):
        # 列表与元组均可；匹配始终按文件行序，与序列内顺序无关。
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
                self.assertEqual(matches, self.EXPECTED_MATCHES)
                self.assertEqual(warnings, self.EXPECTED_WARNINGS)
                # 返回值仍是行号和文本的列表。
                self.assertIsInstance(matches, list)
                self.assertIsInstance(warnings, list)

    def test_single_string_matches_single_element_sequence(self):
        # 单个 "r-1" 字符串与只含 "r-1" 的列表、元组结果完全相同：
        # 仅第 4 行（第 5 行是 INFO），坏行警告不变。
        expected_matches = [(4, CORE_LINES[3])]
        results = [
            iter_matches(CORE_LINES, "ERROR", "r-1"),
            iter_matches(CORE_LINES, "ERROR", ["r-1"]),
            iter_matches(CORE_LINES, "ERROR", ("r-1",)),
        ]
        for matches, warnings in results:
            self.assertEqual(matches, expected_matches)
            self.assertEqual(warnings, self.EXPECTED_WARNINGS)

    def test_whitespace_value_and_nonmatching_shapes(self):
        # 同一组物理行：含空格的标识原样比较；大小写不同与子串不匹配。
        matches, warnings = iter_matches(
            CORE_LINES, "ERROR", [" r-1 "]
        )
        self.assertEqual(matches, [(7, CORE_LINES[6])])
        self.assertEqual(warnings, self.EXPECTED_WARNINGS)
        # 选择大小写不同或子串形式的标识时，只会精确命中同值记录，
        # 不会牵连 r-1（区分大小写、不做子串匹配）。
        cases = [
            (["R-1"], [(6, CORE_LINES[5])]),
            (["r-12"], [(8, CORE_LINES[7])]),
            (["r-1", "R-1", "r-12"],
             [(4, CORE_LINES[3]), (6, CORE_LINES[5]), (8, CORE_LINES[7])]),
        ]
        for request_ids, expected in cases:
            with self.subTest(request_ids):
                matches, warnings = iter_matches(
                    CORE_LINES, "ERROR", request_ids
                )
                self.assertEqual(matches, expected)
                self.assertEqual(warnings, self.EXPECTED_WARNINGS)
        # 缺失、null、数字、仅嵌套出现的顶层标识静默跳过：无警告。
        lines = [
            '{"level":"ERROR"}',
            '{"level":"ERROR","request_id":null}',
            '{"level":"ERROR","request_id":42}',
            '{"level":"ERROR","ctx":{"request_id":"r-1"}}',
        ]
        matches, warnings = iter_matches(lines, "ERROR", ["r-1"])
        self.assertEqual(matches, [])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
