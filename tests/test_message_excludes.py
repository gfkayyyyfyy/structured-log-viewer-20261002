"""--message-excludes 消息子串排除的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json），
测试输入均由各用例在临时目录中以 UTF-8 自行生成并在结束时清理，
不依赖 sample.jsonl、demo.jsonl、cr.jsonl、网络或外部服务；
产品代码、其余筛选条件、摘要、导出与 iter_matches 返回约定保持不变。

覆盖要点（标准输出、标准错误、退出码分别断言，失败信息可定位差异）：
- 固定样例连续 8 行均为合法 ERROR 对象，顶层 message 依次为
  "timeout"、"timeout retry"、"Timeout"、"retry"、缺失、null、
  数字 42，以及仅在 nested 中出现 "timeout" 而顶层缺失。
  选择 ERROR 并排除 timeout、retry 两个子串后，只输出原始第
  3、5、6、7、8 行：大小写不命中（Timeout）与 message 缺失、为 null、
  非字符串或只在嵌套对象中的记录都不因排除条件丢弃；输出保持文件顺序
  与原始正文，每行仍为“原行号<Tab>原文”；标准错误为空，退出码 0。
- 排除值的顺序交换或重复不改变输出；不传该选项时 8 行全部输出。
- 同一文件改用 --message-contains timeout 且 --message-excludes retry：
  包含与排除取交集，只输出第 1 行（第 2 行同时命中两者被剔除）。
- 小样例验证 a.*b 按普通文字排除（不当作正则），axb 保留；
  合法排除值的首尾空白参与匹配，不被裁剪。
- 四行诊断边界：--since 启用时间检查时，timestamp 为 null 的行与
  not-json 行分别产出含原行号的“无效日志：timestamp 缺失或格式无效”
  和“无效日志：JSON 解析失败”，按行序每行一条；被排除的合法行静默
  不输出也不警告；退出码 0。
- 排除值为空或全空白（即使另有合法值、路径不存在）：在读取文件之前
  拒绝，标准输出为空，标准错误只有
  “参数错误：--message-excludes 不能为空或全为空白”，退出码 2。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 固定样例的 8 个物理行（不含行分隔符），全部是合法 ERROR 对象。
# 行号在样例中固定，断言显式写出预期行号，不随行序变化隐式推导。
# ---------------------------------------------------------------------------
LINE1 = '{"level":"ERROR","message":"timeout"}'
LINE2 = '{"level":"ERROR","message":"timeout retry"}'
# 大写 T：排除值区分大小写，"Timeout" 不含子串 "timeout"。
LINE3 = '{"level":"ERROR","message":"Timeout"}'
LINE4 = '{"level":"ERROR","message":"retry"}'
# 顶层 message 缺失：不因排除条件丢弃。
LINE5 = '{"level":"ERROR"}'
# message 为 null：不因排除条件丢弃。
LINE6 = '{"level":"ERROR","message":null}'
# message 为数字：非字符串，不因排除条件丢弃。
LINE7 = '{"level":"ERROR","message":42}'
# "timeout" 只出现在嵌套对象中，顶层 message 缺失：不触及嵌套字段。
LINE8 = '{"level":"ERROR","nested":{"message":"timeout"}}'
FIXTURE_LINES = [
    LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7, LINE8,
]
F_PAYLOAD = ("\n".join(FIXTURE_LINES) + "\n").encode("utf-8")

# 选择 ERROR 并排除 timeout、retry 后保留的行：第 3、5、6、7、8 行。
F_KEPT_STDOUT = "".join(
    f"{i}\t{FIXTURE_LINES[i - 1]}\n" for i in (3, 5, 6, 7, 8)
)

ARG_ERROR = "参数错误：--message-excludes 不能为空或全为空白\n"


def _numbered(*indices):
    """按给定行号（从 1 开始）拼成 ``行号<Tab>原文`` 的预期标准输出。"""
    return "".join(f"{i}\t{FIXTURE_LINES[i - 1]}\n" for i in indices)


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


class MessageExcludesFilterTests(unittest.TestCase):
    """--message-excludes 的匹配语义：只查顶层、解码后、区分大小写的子串。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "messages.jsonl"
        path.write_bytes(F_PAYLOAD)
        self.path = str(path)

    def _run_excludes(self, *needles):
        """选择 ERROR 并按给定顺序重复提供 --message-excludes。"""
        args = [self.path, "--level", "ERROR"]
        for needle in needles:
            args.extend(["--message-excludes", needle])
        return run_cli(*args)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：8 个物理行均为含 ERROR 级别的合法 JSON 对象，
        # 各行顶层 message 的形状与文字与用例约定一致。
        self.assertEqual(len(FIXTURE_LINES), 8)
        self.assertEqual(F_PAYLOAD.count(b"\n"), 8)
        records = [json.loads(line) for line in FIXTURE_LINES]
        self.assertEqual([record["level"] for record in records],
                         ["ERROR"] * 8)
        self.assertEqual(records[0]["message"], "timeout")
        self.assertEqual(records[1]["message"], "timeout retry")
        self.assertEqual(records[2]["message"], "Timeout")
        self.assertEqual(records[3]["message"], "retry")
        self.assertNotIn("message", records[4])
        self.assertIsNone(records[5]["message"])
        self.assertEqual(records[6]["message"], 42)
        self.assertNotIn("message", records[7])
        self.assertEqual(records[7]["nested"]["message"], "timeout")

    def test_excluding_timeout_and_retry_keeps_other_lines(self):
        # 排除 timeout、retry：第 1、2、4 行命中至少一个排除值被剔除；
        # 第 3 行大小写不命中，第 5/6/7 行 message 缺失/null/数字，
        # 第 8 行只在嵌套对象中出现 timeout——全部保留。
        proc = self._run_excludes("timeout", "retry")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, F_KEPT_STDOUT)
        self.assertEqual(proc.stderr, "")

    def test_swapping_exclude_value_order_gives_same_output(self):
        # 先 retry 后 timeout：匹配集合与输出逐字节相同。
        proc = self._run_excludes("retry", "timeout")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, F_KEPT_STDOUT)
        self.assertEqual(proc.stderr, "")

    def test_duplicate_exclude_values_give_same_output(self):
        for needles in (
            ("timeout", "timeout", "retry"),
            ("retry", "retry", "timeout"),
            ("timeout", "retry", "timeout", "retry"),
            ("retry", "timeout", "timeout"),
        ):
            with self.subTest(needles):
                proc = self._run_excludes(*needles)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, F_KEPT_STDOUT)
                self.assertEqual(proc.stderr, "")

    def test_without_option_all_eight_lines_output(self):
        # 不传 --message-excludes：8 行合法 ERROR 记录全部按原序输出，
        # 行号、制表符与完整原文保持不变。
        proc = run_cli(self.path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(1, 2, 3, 4, 5, 6, 7, 8))
        self.assertEqual(proc.stderr, "")

    def test_contains_timeout_and_excludes_retry_only_first_line(self):
        # 包含条件与排除条件取交集：第 1 行含 timeout 且不含 retry，
        # 唯一输出；第 2 行同时含两者被排除；第 3 行 "Timeout" 不满足
        # 区分大小写的包含条件；其余行不含 timeout 子串。
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-contains", "timeout",
            "--message-excludes", "retry",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(1))
        self.assertEqual(proc.stderr, "")

    def test_metacharacters_are_literal_text(self):
        # 小样例：a.*b 作为排除值按普通文字匹配。若被误当作正则，
        # "a.*b" 会匹配 axb 导致第 2 行也被剔除；逐字匹配时只剔除
        # 第 1 行，axb 保留。
        lines = [
            '{"level":"ERROR","message":"a.*b"}',
            '{"level":"ERROR","message":"axb"}',
        ]
        path = Path(self._tmp.name) / "literal.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-excludes", "a.*b",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"2\t{lines[1]}\n")
        self.assertEqual(proc.stderr, "")

    def test_surrounding_spaces_of_exclude_value_participate(self):
        # 合法排除值的首尾空白原样参与匹配：
        # 第 1 行消息含 " timeout " 被剔除；若排除值首尾空格被裁剪，
        # 第 2 行 "timeout" 也会被错误剔除。
        lines = [
            '{"level":"ERROR","message":" timeout "}',
            '{"level":"ERROR","message":"timeout"}',
        ]
        path = Path(self._tmp.name) / "spaces.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-excludes", " timeout ",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"2\t{lines[1]}\n")
        self.assertEqual(proc.stderr, "")
        # 对照：去掉排除值首尾空白后两行都含 timeout，全部被剔除。
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-excludes", "timeout",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")


class MessageExcludesDiagnosticsTests(unittest.TestCase):
    """启用 --since 时排除条件不遮蔽无效行诊断，边界逐行核对。"""

    def test_since_with_excludes_outputs_and_warns_by_line(self):
        lines = [
            # 1 合法记录，时间不早于起点，消息不被排除：唯一输出。
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"keep"}',
            # 2 timestamp 为 null：先于排除检查产出时间戳警告并跳过。
            '{"level":"ERROR","timestamp":null,"message":"timeout"}',
            # 3 根本不是 JSON：解析失败警告。
            "not-json",
            # 4 时间合法但消息命中排除值：静默不输出、不警告。
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"timeout"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "diagnostics.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--since", "2026-10-03T10:00:00Z",
                "--message-excludes", "timeout",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有第 1 行：原行号、一个制表符、原始正文。
        self.assertEqual(proc.stdout, f"1\t{lines[0]}\n")
        # 标准错误按文件行序依次是第 2 行的 timestamp 诊断和第 3 行的
        # JSON 解析诊断，均带原行号，每行一条；第 4 行静默无警告。
        self.assertEqual(
            proc.stderr,
            "第 2 行：无效日志：timestamp 缺失或格式无效\n"
            "第 3 行：无效日志：JSON 解析失败\n",
        )


class MessageExcludesArgumentTests(unittest.TestCase):
    """空或全空白排除值：读取文件之前拒绝，退出码 2 且标准输出为空。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用确定不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")

    def _assert_blank_rejected(self, needles):
        args = [self.missing, "--level", "ERROR"]
        for needle in needles:
            args.extend(["--message-excludes", needle])
        proc = run_cli(*args)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # 只有这一条参数错误：不夹杂 argparse 用法或文件读取失败信息。
        self.assertEqual(proc.stderr, ARG_ERROR)

    def test_empty_or_blank_value_is_argument_error(self):
        for value in ("", "   ", "\t", " \t "):
            with self.subTest(repr(value)):
                self._assert_blank_rejected((value,))

    def test_blank_value_rejected_even_with_other_valid_values(self):
        # 即使另有合法值、且文件路径不存在，也只报告同一条参数错误。
        for needles in (
            ("timeout", ""),
            ("", "timeout"),
            ("timeout", "   "),
            ("retry", "  \t", "timeout"),
            ("\t", "timeout", "retry"),
        ):
            with self.subTest(needles):
                self._assert_blank_rejected(needles)


if __name__ == "__main__":
    unittest.main()
