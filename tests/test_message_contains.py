"""--message-contains 消息子串筛选的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json），
测试输入均由各用例在临时目录中以 UTF-8 自行生成，不依赖 sample.jsonl、
cr.jsonl、网络或外部服务；产品代码、公开命令参数与模块接口保持不变。

覆盖要点（标准输出、标准错误、退出码分别断言，失败信息可定位差异）：
- 子串匹配只检查顶层 message 字符串，按 JSON 解码后的文字做区分大小写的
  连续子串匹配：双方都保留首尾空白；星号、句点按普通文字处理，不解释为
  正则。同级别消息 ``a.*b`` 与 ``axb`` 用 ``a.*b`` 筛选只命中前者；
  ``Error`` 与 ``error`` 用 ``Error`` 筛选只命中前者。
- 带首尾空格的中文检索值不被裁剪；中文直写与对应 ``\\uXXXX`` 转义的消息
  按解码后文字得到相同匹配结果，输出仍为各自原始正文。
- message 缺失、为 null、为数字或只出现在嵌套对象中时静默不匹配、无警告；
  不传该选项时这些记录仍按级别参与筛选。
- 消息条件与级别条件、时间条件取交集：给出合法 --since 后，即使消息不
  匹配，级别合法而 timestamp 缺失的记录仍被跳过并产生一条含原始行号的
  timestamp 警告；早于起点或消息不匹配的记录静默跳过。
- 固定样例含空白行与损坏 JSON：明确预期行号与原文顺序；损坏行只产生一条
  含行号的 JSON 解析失败警告，空白行没有警告，正常完成退出码 0。
- 选项值为空字符串或全为空白：标准输出为空、退出码 2、标准错误含参数错误
  与选项名，且在读取文件之前拒绝（不存在的路径也不出现文件读取失败）；
  选项缺值同样退出 2 且标准错误指明该选项。
- 合法但无匹配且无无效记录：两条输出流均为空，退出码 0。

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
# 固定样例的 13 个物理行（不含行分隔符）。
# 第 5 行为空白行，第 6 行为损坏 JSON，其余均为合法对象。
# 行号在样例中固定，断言显式写出预期行号，不随行序变化隐式推导。
# ---------------------------------------------------------------------------
LINE1 = '{"level":"ERROR","message":"a.*b"}'
LINE2 = '{"level":"ERROR","message":"axb"}'
LINE3 = '{"level":"ERROR","message":"Error"}'
LINE4 = '{"level":"ERROR","message":"error"}'
LINE5 = ""
LINE6 = "not-json"
LINE7 = '{"level":"ERROR","message":" 中文 检索 "}'
LINE8 = '{"level":"ERROR","message":"中文 检索"}'
LINE9 = '{"level":"ERROR"}'
LINE10 = '{"level":"ERROR","message":null}'
LINE11 = '{"level":"ERROR","message":42}'
LINE12 = '{"level":"ERROR","nested":{"message":" 中文 检索 "}}'
LINE13 = '{"level":"INFO","message":"a.*b"}'
FIXTURE_LINES = [
    LINE1, LINE2, LINE3, LINE4, LINE5, LINE6,
    LINE7, LINE8, LINE9, LINE10, LINE11, LINE12, LINE13,
]
F_PAYLOAD = ("\n".join(FIXTURE_LINES) + "\n").encode("utf-8")

# 空白行无警告；损坏行只产生一条含原始行号的 JSON 解析失败警告。
F_STDERR = "第 6 行：无效日志：JSON 解析失败\n"


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


class MessageContainsFilterTests(unittest.TestCase):
    """--message-contains 的匹配语义：只查顶层、解码后、区分大小写的子串。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "messages.jsonl"
        path.write_bytes(F_PAYLOAD)
        self.path = str(path)

    def _run(self, needle, *levels):
        levels = levels or ("ERROR",)
        args = []
        for level in levels:
            args.extend(["--level", level])
        args.extend(["--message-contains", needle])
        return run_cli(self.path, *args)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：13 个物理行，第 5 行空白、第 6 行损坏，行号约定成立。
        self.assertEqual(len(FIXTURE_LINES), 13)
        self.assertEqual(F_PAYLOAD.count(b"\n"), 13)
        self.assertEqual(LINE5, "")
        self.assertEqual(LINE6, "not-json")

    def test_metacharacters_are_literal_and_match_is_case_sensitive(self):
        # a.*b 作为普通文字：只命中第 1 行；若被当作正则，第 2 行 axb 也会
        # 被 .* 匹配而错误命中。同级别的第 13 行因级别不同不参与。
        proc = self._run("a.*b")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(1))
        self.assertEqual(proc.stderr, F_STDERR)
        # 区分大小写：Error 只命中第 3 行，第 4 行小写 error 不命中。
        proc = self._run("Error")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(3))
        self.assertEqual(proc.stderr, F_STDERR)

    def test_search_value_surrounding_spaces_and_chinese_not_trimmed(self):
        # 检索值首尾空格不被裁剪：只有第 7 行（解码后首尾带空格）命中，
        # 第 8 行“中文 检索”无首尾空格不命中；嵌套对象中的同文（第 12 行）
        # 不在顶层 message 中，不命中。
        proc = self._run(" 中文 检索 ")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(7))
        self.assertEqual(proc.stderr, F_STDERR)
        # 去掉检索值首尾空格后为连续子串：第 7、8 行都命中，保持原文行序；
        # 仍不触及第 12 行的嵌套字段。
        proc = self._run("中文 检索")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(7, 8))
        self.assertEqual(proc.stderr, F_STDERR)

    def test_chinese_literal_and_unicode_escape_messages_match_equally(self):
        # 同一 message 的两种 JSON 写法：中文直写与 ASCII 的 \uXXXX 转义。
        literal = '{"level":"ERROR","message":" 中文 检索 "}'
        escaped = json.dumps(
            {"level": "ERROR", "message": " 中文 检索 "},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        # 自检：两条原始正文确实不同，转义版字节里不含直写中文而含 \u。
        self.assertNotEqual(literal, escaped)
        self.assertIn("\\u", escaped)
        self.assertNotIn("中文", escaped)
        path = Path(self._tmp.name) / "escaped.jsonl"
        path.write_text(literal + "\n" + escaped + "\n", encoding="utf-8")

        # 按 JSON 解码后的文字比较：直写中文检索值对两种写法同样命中，
        # 输出仍为各自原始正文（转义版不重新序列化），行号与原序固定。
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-contains", " 中文 检索 ",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"1\t{literal}\n2\t{escaped}\n")
        self.assertEqual(proc.stderr, "")
        # 反方向：把转义序列的原文（反斜杠+u+十六进制，共六个 ASCII 字符）
        # 当普通文字检索时，解码后的文字里并不存在该字面序列，因此连转义版
        # 记录也不命中——匹配发生在解码后的文字上，而非原始字节。
        literal_escape = "".join(["\\", "u4e2d"])
        self.assertEqual(literal_escape, "\\u4e2d")  # 六个字符：\ u 4 e 2 d
        proc = run_cli(
            str(path), "--level", "ERROR",
            "--message-contains", literal_escape,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_missing_null_number_and_nested_message_silently_skip(self):
        # message 缺失（9）、null（10）、数字（11）、只在嵌套对象中（12）
        # 均静默不匹配，不产生任何警告；损坏行警告照旧只有一条。
        proc = self._run(" 中文 检索 ")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(7))
        self.assertEqual(proc.stderr, F_STDERR)
        # 换一个这些行都不可能包含的子串：标准输出为空，标准错误仍只有
        # 损坏行那一条，message 形状问题不产生警告。
        proc = self._run("不可能出现的消息内容")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, F_STDERR)

    def test_without_option_records_still_participate_by_level(self):
        # 不传 --message-contains：message 缺失/null/数字/嵌套的第 9-12 行
        # 仍按 ERROR 级别输出；空白行跳过，损坏行仅一条警告，INFO 行不输出。
        proc = run_cli(self.path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, _numbered(1, 2, 3, 4, 7, 8, 9, 10, 11, 12)
        )
        self.assertEqual(proc.stderr, F_STDERR)

    def test_message_condition_intersects_with_selected_levels(self):
        # 同时选择 ERROR 与 INFO：a.*b 命中第 1 行（ERROR）和第 13 行
        # （INFO），第 2 行 axb 因子串不匹配仍排除——两条件取交集。
        proc = self._run("a.*b", "ERROR", "INFO")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(1, 13))
        self.assertEqual(proc.stderr, F_STDERR)
        # 只选 INFO 时只剩第 13 行，级别条件独立生效。
        proc = self._run("a.*b", "INFO")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(13))
        self.assertEqual(proc.stderr, F_STDERR)


class MessageSinceIntersectionTests(unittest.TestCase):
    """消息条件与 --since 时间条件取交集，时间校验不受消息匹配影响。"""

    def test_since_applies_even_when_message_does_not_match(self):
        lines = [
            # 1 级别合法、时间不早于起点、消息命中：唯一输出。
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"命中"}',
            # 2 早于起点：消息虽命中，静默跳过。
            '{"level":"ERROR","timestamp":"2026-10-03T09:00:00Z","message":"命中"}',
            # 3 timestamp 缺失、消息命中：跳过并产生时间戳警告。
            '{"level":"ERROR","message":"命中"}',
            # 4 级别不在所选集合：时间合法，静默跳过。
            '{"level":"INFO","timestamp":"2026-10-03T10:00:00Z","message":"命中"}',
            # 5 timestamp 缺失且消息也不匹配：仍产生时间戳警告（只检查一次）。
            '{"level":"ERROR","message":"别的"}',
            # 6 时间合法但消息不匹配：静默跳过。
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"别的"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "since.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--since", "2026-10-03T10:00:00Z",
                "--message-contains", "命中",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 交集：只有第 1 行同时满足级别、时间、消息三个条件。
        self.assertEqual(proc.stdout, "1\t" + lines[0] + "\n")
        # 第 3、5 行各一条含原始行号的 timestamp 警告，按行序排列；
        # 其余被排除的行静默，不产生警告。
        self.assertEqual(
            proc.stderr,
            "第 3 行：无效日志：timestamp 缺失或格式无效\n"
            "第 5 行：无效日志：timestamp 缺失或格式无效\n",
        )


class MessageContainsArgumentTests(unittest.TestCase):
    """参数问题退出码 2 且标准输出为空；无匹配时安静地以 0 结束。"""

    def test_empty_or_blank_value_is_argument_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            path.write_text(
                '{"level":"ERROR","message":"x"}\n', encoding="utf-8"
            )
            for value in ("", "   ", "\t", " \t "):
                with self.subTest(repr(value)):
                    proc = run_cli(
                        str(path), "--level", "ERROR",
                        "--message-contains", value,
                    )
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--message-contains", proc.stderr)
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_blank_value_rejected_before_reading_file(self):
        # 文件不存在也不读取：只报参数错误，不出现文件读取失败。
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            for value in ("", "  \t"):
                with self.subTest(repr(value)):
                    proc = run_cli(
                        missing, "--level", "ERROR",
                        "--message-contains", value,
                    )
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn("--message-contains", proc.stderr)
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_missing_option_value(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            path.write_text(
                '{"level":"ERROR","message":"x"}\n', encoding="utf-8"
            )
            # 选项后缺值：argparse 在读取文件前报错，错误说明含选项名。
            proc = run_cli(str(path), "--level", "ERROR", "--message-contains")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("--message-contains", proc.stderr)

    def test_valid_but_no_match_and_no_invalid_records_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nomatch.jsonl"
            path.write_text(
                '{"level":"ERROR","message":"aaa"}\n'
                '{"level":"INFO","message":"bbb"}\n',
                encoding="utf-8",
            )
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--message-contains", "zzz",
            )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
