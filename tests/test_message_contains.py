"""--message-contains 消息子串筛选的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令参数
与模块接口均保持不变，仅验证 --message-contains 这一条筛选路径。

覆盖要点：
- 只检查顶层 message 字符串，按 JSON 解码后的文字做区分大小写的连续
  子串匹配，双方保留首尾空白，星号与句点按普通文字处理：
  同级别消息 a.*b 与 axb 用 a.*b 筛选只命中前者；
  Error 与 error 用 Error 筛选只命中前者；
  带空格的中文检索值不被裁剪；中文直写与对应 Unicode 转义的消息
  匹配结果相同。
- message 缺失、为 null、为数字或只出现在嵌套对象时静默不匹配；
  不传该选项时这些记录仍按级别参与筛选。
- 固定样例含空白行与损坏 JSON：明确预期行号与原文顺序，损坏行只产生
  一条含行号的 JSON 解析失败警告，空白行无警告，退出码为 0。
- 消息条件与级别条件取交集；给出合法 --since 后，即使消息不匹配，
  级别合法而 timestamp 缺失的记录仍被跳过并产生一条含原始行号的
  timestamp 缺失或格式无效警告。
- 选项值为空字符串或全空白：标准输出为空、退出码 2、标准错误含
  参数错误与选项名；用不存在的文件路径确认此时不出现文件读取失败。
- 选项缺值：退出码 2，标准输出为空，标准错误指明该选项。
- 合法但无匹配且无无效记录：两条输出流均为空，退出码 0。

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 匹配语义样例的物理正文（不含行分隔符），level 均为 ERROR。
# ---------------------------------------------------------------------------
# 第 1 行：message 含星号与句点，筛选值 a.*b 按普通文字命中本行。
LIT_DOT_STAR = '{"level":"ERROR","message":"前缀 a.*b 后缀"}'
# 第 2 行：message 为 axb；a.*b 不是正则，不得命中本行。
LIT_PLAIN = '{"level":"ERROR","message":"前缀 axb 后缀"}'
# 第 3 行：大写 Error，筛选值 Error 区分大小写命中本行。
CASE_UPPER = '{"level":"ERROR","message":"Error 发生"}'
# 第 4 行：小写 error，不得被 Error 命中。
CASE_LOWER = '{"level":"ERROR","message":"error 发生"}'
# 第 5 行：中文两侧有空格，带空格的检索值 " 中文 检索 " 命中本行。
CN_SPACED = '{"level":"ERROR","message":"前缀 中文 检索 后缀"}'
# 第 6 行：中文两侧无空格；检索值的首尾空格不被裁剪，故不得命中本行。
CN_TIGHT = '{"level":"ERROR","message":"前缀中文检索后缀"}'
# 第 7 行：中文直写。
CN_DIRECT = '{"level":"ERROR","message":"中文直写"}'
# 第 8 行：与第 7 行相同文字的 Unicode 转写形式，解码后文字相同，
# 用同一中文检索值筛选应有相同匹配结果；原始正文中的转义序列原样保留。
CN_ESCAPED = '{"level":"ERROR","message":"\\u4e2d\\u6587\\u76f4\\u5199"}'


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


class MessageContainsTestCase(unittest.TestCase):
    """公共基类：每个用例一个临时目录，按行写 UTF-8 JSONL 文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, lines, name="case.jsonl"):
        """把 lines 用 LF 连接（末尾补 LF）写入临时文件，返回路径字符串。"""
        path = Path(self._tmp.name) / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _missing_path(self):
        # 不存在的文件路径：用于确认参数校验发生在读取文件之前。
        return str(Path(self._tmp.name) / "不存在的文件.jsonl")


class LiteralAndCaseTests(MessageContainsTestCase):
    """星号、句点按普通文字处理；匹配区分大小写。"""

    def test_fixture_layout_matches_spec(self):
        # 数据自检：a.*b 与 axb 仅在星号、句点上有差异；Error/error 仅大小写不同。
        self.assertIn("a.*b", LIT_DOT_STAR)
        self.assertIn("axb", LIT_PLAIN)
        self.assertNotIn("a.*b", LIT_PLAIN)
        self.assertIn('"Error ', CASE_UPPER)
        self.assertIn('"error ', CASE_LOWER)
        # 第 8 行原文确为 ASCII 转义序列，解码后与第 7 行文字相同。
        self.assertNotIn("中文", CN_ESCAPED)
        self.assertIn("\\u4e2d", CN_ESCAPED)

    def test_dot_and_star_are_literal_characters(self):
        path = self._write([LIT_DOT_STAR, LIT_PLAIN])
        proc = run_cli(path, "--level", "ERROR", "--message-contains", "a.*b")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只命中第 1 行：a.*b 按普通文字匹配，不作为正则命中 axb。
        self.assertEqual(proc.stdout, "1\t" + LIT_DOT_STAR + "\n")
        self.assertEqual(proc.stderr, "")

    def test_matching_is_case_sensitive(self):
        path = self._write([CASE_UPPER, CASE_LOWER])
        proc = run_cli(path, "--level", "ERROR", "--message-contains", "Error")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只命中大写 Error 的第 1 行，小写 error 不匹配。
        self.assertEqual(proc.stdout, "1\t" + CASE_UPPER + "\n")
        self.assertEqual(proc.stderr, "")

    def test_chinese_value_with_spaces_is_not_trimmed(self):
        path = self._write([CN_SPACED, CN_TIGHT])
        proc = run_cli(
            path, "--level", "ERROR", "--message-contains", " 中文 检索 "
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 检索值首尾空格保留：只命中中文两侧有空格的第 1 行；
        # 若值被裁剪为“中文 检索”，无空格的第 2 行也会命中。
        self.assertEqual(proc.stdout, "1\t" + CN_SPACED + "\n")
        self.assertEqual(proc.stderr, "")

    def test_chinese_direct_and_unicode_escape_match_identically(self):
        path = self._write([CN_DIRECT, CN_ESCAPED])
        proc = run_cli(path, "--level", "ERROR", "--message-contains", "中文")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 直写与 Unicode 转写的消息解码后文字相同，两行都命中；
        # 输出按原文顺序，转写行的原始转义序列逐字节保留、不重新序列化。
        self.assertEqual(
            proc.stdout,
            "1\t" + CN_DIRECT + "\n"
            "2\t" + CN_ESCAPED + "\n",
        )
        self.assertEqual(proc.stderr, "")


class NonStringMessageTests(MessageContainsTestCase):
    """message 缺失、为 null、为数字或只在嵌套对象中时静默不匹配。"""

    # 五行 level 均为 ERROR：message 分别缺失、为 null、为数字、
    # 只出现在嵌套对象、本身是非字符串对象。
    NON_STRING_LINES = [
        '{"level":"ERROR","detail":"无消息"}',
        '{"level":"ERROR","message":null}',
        '{"level":"ERROR","message":42}',
        '{"level":"ERROR","detail":{"message":"目标"}}',
        '{"level":"ERROR","message":{"nested":"目标"}}',
    ]

    def test_non_string_or_nested_message_silently_skipped(self):
        path = self._write(self.NON_STRING_LINES)
        proc = run_cli(path, "--level", "ERROR", "--message-contains", "目标")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 静默不匹配：标准输出为空，且不产生任何警告。
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_without_option_those_records_still_match_by_level(self):
        path = self._write(self.NON_STRING_LINES)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 不传 --message-contains 时，这些记录仍按级别参与筛选，
        # 五行全部按原文顺序输出，行号连续。
        self.assertEqual(
            proc.stdout,
            "".join(
                f"{lineno}\t{raw}\n"
                for lineno, raw in enumerate(self.NON_STRING_LINES, start=1)
            ),
        )
        self.assertEqual(proc.stderr, "")


class BlankAndBrokenLineTests(MessageContainsTestCase):
    """空白行无警告；损坏行只产生一条含行号的 JSON 解析失败警告。"""

    # 第 1、5 行命中；第 2 行为空行，第 3 行仅空白（均无警告）；
    # 第 4 行是损坏 JSON（恰好一条警告）。
    LINES = [
        '{"level":"ERROR","message":"目标甲"}',
        "",
        "   ",
        "not-json",
        '{"level":"ERROR","message":"目标乙"}',
    ]

    def test_blank_and_broken_lines_with_message_filter(self):
        path = self._write(self.LINES)
        proc = run_cli(path, "--level", "ERROR", "--message-contains", "目标")
        # 正常完成，退出码为 0。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 命中行按原文顺序输出，行号为原始物理行号。
        self.assertEqual(
            proc.stdout,
            "1\t" + self.LINES[0] + "\n"
            "5\t" + self.LINES[4] + "\n",
        )
        # 标准错误只有第 4 行的一条 JSON 解析失败警告；空白行没有警告。
        self.assertEqual(proc.stderr, "第 4 行：无效日志：JSON 解析失败\n")
        self.assertEqual(proc.stderr.count("JSON 解析失败"), 1)
        self.assertNotIn("第 2 行", proc.stderr)
        self.assertNotIn("第 3 行", proc.stderr)


class IntersectionAndSinceTests(MessageContainsTestCase):
    """消息条件与级别条件取交集；--since 启用后 timestamp 检查先于消息判断。"""

    # 第 1 行：级别与消息都命中，时间在区间内，应输出。
    # 第 2 行：消息命中但级别不在所选集合内，不输出。
    # 第 3 行：级别命中但消息不含检索值，不输出。
    # 第 4 行：级别合法（且被选中）但 timestamp 缺失；即使消息不匹配，
    # 给出 --since 后仍被跳过并产生一条含原始行号的 timestamp 警告。
    LINES = [
        '{"level":"ERROR","timestamp":"2026-10-03T10:00:01Z","message":"命中"}',
        '{"level":"INFO","timestamp":"2026-10-03T10:00:01Z","message":"命中"}',
        '{"level":"ERROR","timestamp":"2026-10-03T10:00:01Z","message":"其他"}',
        '{"level":"ERROR","message":"无关"}',
    ]

    def test_message_and_level_conditions_intersect(self):
        path = self._write(self.LINES[:3])
        proc = run_cli(
            path, "--level", "ERROR", "--message-contains", "命中"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只有级别与消息同时满足的第 1 行输出。
        self.assertEqual(proc.stdout, "1\t" + self.LINES[0] + "\n")
        self.assertEqual(proc.stderr, "")

    def test_since_skips_missing_timestamp_even_when_message_differs(self):
        path = self._write(self.LINES)
        proc = run_cli(
            path,
            "--level", "ERROR",
            "--message-contains", "命中",
            "--since", "2026-10-03T10:00:00Z",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 第 1 行正常命中输出。
        self.assertEqual(proc.stdout, "1\t" + self.LINES[0] + "\n")
        # 第 4 行消息虽不匹配，但级别合法且启用了时间筛选：
        # timestamp 缺失仍产生恰好一条含原始行号的警告。
        self.assertEqual(
            proc.stderr, "第 4 行：无效日志：timestamp 缺失或格式无效\n"
        )


class InvalidOptionValueTests(MessageContainsTestCase):
    """空值、全空白值与缺值都是参数错误：退出码 2，标准输出为空。"""

    def test_empty_value_is_parameter_error_before_reading_file(self):
        proc = run_cli(
            self._missing_path(),
            "--level", "ERROR",
            "--message-contains", "",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # 标准错误包含参数错误与选项名。
        self.assertIn("参数错误", proc.stderr)
        self.assertIn("--message-contains", proc.stderr)
        # 参数校验在读取文件之前：路径不存在也不出现文件读取失败。
        self.assertNotIn("文件读取失败", proc.stderr)

    def test_whitespace_only_value_is_parameter_error_before_reading_file(self):
        proc = run_cli(
            self._missing_path(),
            "--level", "ERROR",
            "--message-contains", "  \t ",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("参数错误", proc.stderr)
        self.assertIn("--message-contains", proc.stderr)
        self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_option_value_is_parameter_error(self):
        # --message-contains 之后没有值：argparse 拒绝，退出码同样为 2。
        proc = run_cli(
            self._missing_path(), "--level", "ERROR", "--message-contains"
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        # 标准错误指明该选项。
        self.assertIn("--message-contains", proc.stderr)
        self.assertNotIn("文件读取失败", proc.stderr)


class NoMatchTests(MessageContainsTestCase):
    """合法但无匹配且无无效记录：两条输出流均为空，退出码为 0。"""

    def test_no_match_and_no_invalid_records(self):
        lines = [
            '{"level":"ERROR","message":"完全无关"}',
            '{"level":"ERROR","message":"另一条"}',
        ]
        path = self._write(lines)
        proc = run_cli(
            path, "--level", "ERROR", "--message-contains", "不存在的子串"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
