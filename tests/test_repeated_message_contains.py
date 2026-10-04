"""重复 --message-contains（消息候选并集）的离线回归测试。

验证新行为：重复提供 --message-contains 时，多个候选按 JSON 解码后的
文字各自做区分大小写的连续子串匹配，顶层 message 包含任一候选即满足
消息条件，再与级别、请求标识和时间条件取交集；iter_matches 同样接受
候选序列，而单个字符串或不传该参数的旧调用约定保持不变。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中生成并在结束时清理，
不依赖 demo.jsonl、sample.jsonl、cr.jsonl 或外部服务。

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
# 第 1 行 timeout / ERROR；第 2 行 retry / ERROR；
# 第 3 行 “timeout retry” / ERROR（一条消息可同时含两个候选）；
# 第 4 行 timeout / INFO（级别不匹配）；第 5 行空白；
# 第 6 行非法 JSON；第 7 行 message 为 null / ERROR（静默不匹配）。
CORE_LINES = [
    '{"level":"ERROR","message":"timeout"}',
    '{"level":"ERROR","message":"retry"}',
    '{"level":"ERROR","message":"timeout retry"}',
    '{"level":"INFO","message":"timeout"}',
    "",
    "not-json",
    '{"level":"ERROR","message":null}',
]
# 选择 ERROR 并给出 timeout、retry 两个候选时：第 1、2、3 行各输出一次，
# 第 3 行虽同时命中两个候选也只输出一次；第 4 行级别不同，第 7 行
# message 为 null 静默不匹配；按原序保留行号、制表符和完整原文。
CORE_STDOUT = (
    "1\t" + CORE_LINES[0] + "\n"
    "2\t" + CORE_LINES[1] + "\n"
    "3\t" + CORE_LINES[2] + "\n"
)
CORE_STDERR = "第 6 行：无效日志：JSON 解析失败\n"


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


class RepeatedMessageContainsFilterTests(unittest.TestCase):
    """重复 --message-contains：候选取并集后与其他条件取交集。"""

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

    def test_two_candidates_select_union_intersected_with_level(self):
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-contains", "timeout", "--message-contains", "retry",
        )
        self._assert_core_result(proc)

    def test_option_order_does_not_matter(self):
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-contains", "retry", "--message-contains", "timeout",
        )
        self._assert_core_result(proc)

    def test_duplicate_candidates_do_not_change_result(self):
        repetitions = (
            ("timeout", "timeout", "retry"),
            ("retry", "retry", "timeout"),
            ("timeout", "retry", "timeout", "retry"),
        )
        for needles in repetitions:
            with self.subTest(needles):
                args = [self.path, "--level", "ERROR"]
                for needle in needles:
                    args += ["--message-contains", needle]
                proc = run_cli(*args)
                self._assert_core_result(proc)

    def test_single_occurrence_keeps_legacy_semantics(self):
        # 只传一次：只有含 timeout 的 ERROR 行（第 1、3 行）。
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-contains", "timeout",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "1\t" + CORE_LINES[0] + "\n3\t" + CORE_LINES[2] + "\n",
        )
        self.assertEqual(proc.stderr, CORE_STDERR)

    def test_without_option_records_still_participate_by_level(self):
        # 不传该选项：第 1、2、3、7 行的 ERROR 记录都输出。
        proc = run_cli(self.path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "".join(
            f"{i}\t{CORE_LINES[i - 1]}\n" for i in (1, 2, 3, 7)
        ))
        self.assertEqual(proc.stderr, CORE_STDERR)

    def test_metacharacters_are_literal_and_match_is_case_sensitive(self):
        # 多个候选同样按普通文字处理，且区分大小写：
        # a.*b 不命中 axb；Error 不命中 error。
        lines = [
            '{"level":"ERROR","message":"a.*b"}',
            '{"level":"ERROR","message":"axb"}',
            '{"level":"ERROR","message":"Error"}',
            '{"level":"ERROR","message":"error"}',
        ]
        path = Path(self._tmp.name) / "literal.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-contains", "a.*b",
            "--message-contains", "Error",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"1\t{lines[0]}\n3\t{lines[2]}\n")
        self.assertEqual(proc.stderr, "")

    def test_surrounding_spaces_of_candidates_are_not_trimmed(self):
        # 候选首尾空白原样参与比较：只有解码后首尾带空格的消息命中。
        lines = [
            '{"level":"ERROR","message":" 命中 "}',
            '{"level":"ERROR","message":"命中"}',
        ]
        path = Path(self._tmp.name) / "spaces.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-contains", " 命中 ",
            "--message-contains", "不存在",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"1\t{lines[0]}\n")
        self.assertEqual(proc.stderr, "")

    def test_missing_null_number_and_nested_message_silently_skip(self):
        # 多候选下，顶层 message 缺失、为 null、为数字或只在嵌套对象中
        # 出现仍静默不匹配，不产生任何警告。
        lines = [
            '{"level":"ERROR"}',
            '{"level":"ERROR","message":null}',
            '{"level":"ERROR","message":42}',
            '{"level":"ERROR","ctx":{"message":"timeout"}}',
            '{"level":"ERROR","message":"timeout"}',
        ]
        path = Path(self._tmp.name) / "shapes.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-contains", "timeout",
            "--message-contains", "retry",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"5\t{lines[4]}\n")
        self.assertEqual(proc.stderr, "")


class RepeatedMessageSinceIntersectionTests(unittest.TestCase):
    """多候选消息条件与 --since 取交集，时间校验不受消息匹配影响。"""

    def test_since_warns_even_when_no_candidate_matches(self):
        lines = [
            # 1 时间合法、消息命中候选：输出。
            '{"level":"ERROR","timestamp":"2026-10-03T11:00:00Z","message":"retry"}',
            # 2 早于起点且消息命中：静默跳过。
            '{"level":"ERROR","timestamp":"2026-10-03T09:00:00Z","message":"timeout"}',
            # 3 消息不匹配且 timestamp 缺失：仍产生一条时间戳警告。
            '{"level":"ERROR","message":"别的"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "since.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--since", "2026-10-03T10:00:00Z",
                "--message-contains", "timeout",
                "--message-contains", "retry",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "1\t" + lines[0] + "\n")
        self.assertEqual(
            proc.stderr,
            "第 3 行：无效日志：timestamp 缺失或格式无效\n",
        )


class RepeatedMessageContainsArgumentErrorTests(unittest.TestCase):
    """--message-contains 多次出现时的参数边界：任一空值即整体拒绝。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用确定不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")

    def test_empty_or_blank_value_rejected_regardless_of_position(self):
        bad_values = ("", "   ", "\t", " \t ")
        for bad in bad_values:
            for position, needles in (
                ("空值在合法值之前", (bad, "timeout")),
                ("空值在合法值之后", ("timeout", bad)),
                ("合法值夹住空值", ("retry", bad, "timeout")),
                ("全部为空或全空白", (bad, "  ")),
            ):
                with self.subTest(value=repr(bad), position=position):
                    args = [self.missing, "--level", "ERROR"]
                    for needle in needles:
                        args += ["--message-contains", needle]
                    proc = run_cli(*args)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--message-contains", proc.stderr)
                    # 参数校验在读取文件之前：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_option_value(self):
        # 某次 --message-contains 缺值：argparse 拒绝，退出码 2，
        # 标准输出为空，错误信息指出该选项；缺值可发生在任一次。
        for args in (
            (self.missing, "--level", "ERROR", "--message-contains"),
            (self.missing, "--level", "ERROR",
             "--message-contains", "timeout", "--message-contains"),
        ):
            with self.subTest(args):
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("--message-contains", proc.stderr)
                self.assertNotIn("文件读取失败", proc.stderr)


class RepeatedMessageContainsModuleApiTests(unittest.TestCase):
    """iter_matches 直接调用：候选序列与单子串字符串的返回约定。"""

    EXPECTED_MATCHES = [
        (1, CORE_LINES[0]),
        (2, CORE_LINES[1]),
        (3, CORE_LINES[2]),
    ]
    EXPECTED_WARNINGS = [(6, "无效日志：JSON 解析失败")]

    def test_list_and_tuple_select_same_union(self):
        # 列表与元组均可；匹配始终按文件行序，与序列内顺序无关；
        # 重复候选不增加输出，一条消息命中多个候选也只出现一次。
        for needles in (
            ["timeout", "retry"],
            ("timeout", "retry"),
            ["retry", "timeout"],
            ("retry", "timeout"),
            ["timeout", "retry", "timeout", "retry"],
        ):
            with self.subTest(needles):
                matches, warnings = iter_matches(
                    CORE_LINES, "ERROR", None, None, None, needles
                )
                self.assertEqual(matches, self.EXPECTED_MATCHES)
                self.assertEqual(warnings, self.EXPECTED_WARNINGS)
                # 返回值仍是行号和文本的列表。
                self.assertIsInstance(matches, list)
                self.assertIsInstance(warnings, list)

    def test_single_string_matches_single_element_sequence(self):
        # 单个 "timeout" 字符串与只含 "timeout" 的列表、元组结果相同：
        # 第 1、3 行（第 3 行的 "timeout retry" 也含该子串）。
        expected = [(1, CORE_LINES[0]), (3, CORE_LINES[2])]
        results = [
            iter_matches(CORE_LINES, "ERROR", None, None, None, "timeout"),
            iter_matches(CORE_LINES, "ERROR", None, None, None, ["timeout"]),
            iter_matches(CORE_LINES, "ERROR", None, None, None, ("timeout",)),
        ]
        for matches, warnings in results:
            self.assertEqual(matches, expected)
            self.assertEqual(warnings, self.EXPECTED_WARNINGS)

    def test_none_means_no_message_check(self):
        # 默认 None：消息为 null 的第 7 行仍按级别输出。
        matches, warnings = iter_matches(CORE_LINES, "ERROR")
        self.assertEqual(
            matches,
            [(i, CORE_LINES[i - 1]) for i in (1, 2, 3, 7)],
        )
        self.assertEqual(warnings, self.EXPECTED_WARNINGS)

    def test_no_match_keeps_invalid_warnings(self):
        matches, warnings = iter_matches(
            CORE_LINES, "ERROR", None, None, None, ["不存在"]
        )
        self.assertEqual(matches, [])
        # 消息不匹配不抑制坏 JSON 行的警告。
        self.assertEqual(warnings, self.EXPECTED_WARNINGS)


if __name__ == "__main__":
    unittest.main()
