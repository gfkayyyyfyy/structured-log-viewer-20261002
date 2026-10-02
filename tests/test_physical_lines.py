"""物理行计数与原文保留的回归测试：正文中易被误当成换行的字符。

覆盖三类正文字符：
- 真实 U+2028 LINE SEPARATOR 与 U+2029 PARAGRAPH SEPARATOR（JSON 字符串
  内的真实字符，不是 \\u2028 转义序列）：某些工具会把它们当作换行，
  本工具必须把它们当作正文，不增加物理行数，也不替换、删除或重新编码；
- 不紧邻 LF 的单独 CR：不是行分隔符，必须原样保留（含文件末尾无 LF 时
  结尾的 CR）；
- 紧邻 LF 的 CR（CRLF）：作为分隔符的一部分被移除，且只移除这一个 CR。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化，以免掩盖正文中 CR 的变化；输出记录自身的终止换行
沿用运行平台现有行为（os.linesep）。

只用 Python 标准库；输入文件在用例自建的临时目录中生成并自动清理，
不依赖 sample.jsonl、网络或外部服务。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 输出记录自身的终止换行沿用运行平台现有行为（子进程 print 的换行翻译），
# 只用于拼接期望输出；正文中的 CR 逐字符断言，不做归一化。
RECORD_TERMINATOR = os.linesep

# 五个物理行的正文（不含行分隔符）。
# 第 1 行：message 中“甲”“乙”之间是真实 U+2028，“乙”“丙”之间是真实
# U+2029（Python 的 \u2028 转义在字符串中产生真实字符，写盘即 UTF-8
# 字节，不是 JSON 文本里的 \\u2028 转义序列）。
LINE1 = (
    '{"level":"ERROR","message":"甲'
    + "\u2028"
    + "乙"
    + "\u2029"
    + '丙"}'
)
# 第 2 行：只含空格和制表符。
LINE2 = " \t \t "
# 第 3 行：非法 JSON。
LINE3 = "not-json"
# 第 4 行：有效但级别不匹配。
LINE4 = '{"level":"INFO"}'
# 第 5 行：前置两个空格，JSON 对象后紧跟一个真实 CR 和一个制表符。
LINE5 = '  {"level":" error ","message":"末条"}\r\t'
FIVE_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5]

# --level ERROR 时：标准输出只含第 1、5 行，原始行号 + 制表符 + 原文，顺序不变；
# 标准错误只有第 3 行的 JSON 解析失败警告；退出码为 0。
EXPECTED_STDOUT = (
    "1\t" + LINE1 + RECORD_TERMINATOR
    + "5\t" + LINE5 + RECORD_TERMINATOR
)
EXPECTED_STDERR = "第 3 行：无效日志：JSON 解析失败" + RECORD_TERMINATOR


def run_cli_bytes(*args):
    """运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：subprocess 的文本模式会启用通用换行，把标准输出中
    的 CR、CRLF 都翻译成 LF，掩盖正文中 CR 的差异。
    """
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定。
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class TrickyBodyCharactersTests(unittest.TestCase):
    """正文中的 U+2028/U+2029 与单独 CR 不影响物理行计数与原文输出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run_payload(self, payload: bytes):
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(payload)
        proc = run_cli_bytes(str(path), "--level", "ERROR")
        # 直接解码字节：UTF-8 解码本身不做任何换行翻译。
        return proc, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def test_fixture_uses_real_unicode_characters_not_json_escapes(self):
        # 测试数据自检：第 1 行含真实 U+2028/U+2029，而不是反斜杠转义形式。
        self.assertIn("\u2028", LINE1)
        self.assertIn("\u2029", LINE1)
        self.assertNotIn("\\u2028", LINE1)
        self.assertNotIn("\\u2029", LINE1)
        # 写入文件后是 UTF-8 编码的真实字符字节。
        self.assertIn(
            ("甲" + "\u2028" + "乙" + "\u2029" + "丙").encode("utf-8"),
            LINE1.encode("utf-8"),
        )
        # 第 5 行的 CR 是真实回车符，位于 JSON 对象之后、制表符之前。
        self.assertTrue(LINE5.endswith("}\r\t"))

    def test_five_line_case_across_separators_and_final_newline(self):
        mixed_with_final = (
            LINE1 + "\r\n" + LINE2 + "\n" + LINE3 + "\r\n"
            + LINE4 + "\n" + LINE5 + "\r\n"
        )
        mixed_without_final = (
            LINE1 + "\n" + LINE2 + "\r\n" + LINE3 + "\n"
            + LINE4 + "\r\n" + LINE5
        )
        payloads = {
            "LF，末行有换行": "\n".join(FIVE_LINES) + "\n",
            "LF，末行无换行": "\n".join(FIVE_LINES),
            "CRLF，末行有换行": "\r\n".join(FIVE_LINES) + "\r\n",
            "CRLF，末行无换行": "\r\n".join(FIVE_LINES),
            "混合分隔，末行有换行": mixed_with_final,
            "混合分隔，末行无换行": mixed_without_final,
        }
        for label, text in payloads.items():
            with self.subTest(label):
                proc, stdout, stderr = self._run_payload(text.encode("utf-8"))
                self.assertEqual(proc.returncode, 0, stderr)
                # 行号 1 和 5 证明 U+2028/U+2029 与行内 CR 都没有被当成
                # 换行（否则后续行号会错位）；正文逐字符相等证明它们没有被
                # 替换、删除或重新编码成 JSON 转义。
                self.assertEqual(stdout, EXPECTED_STDOUT)
                self.assertEqual(stderr, EXPECTED_STDERR)

    def test_trailing_cr_without_lf_is_body_and_kept_verbatim(self):
        # 文件只有一行 {"level":"ERROR"}，末尾是真实 CR、没有 LF：
        # 该 CR 属于正文，必须原样输出，无警告，退出码 0。
        proc, stdout, stderr = self._run_payload(b'{"level":"ERROR"}\r')
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(
            stdout, '1\t{"level":"ERROR"}\r' + RECORD_TERMINATOR
        )
        self.assertEqual(stderr, "")

    def test_crlf_terminator_removes_only_the_separator_cr(self):
        # 同一行改用 CRLF 终止：仅移除作为分隔符的 CR，无警告，退出码 0。
        proc, stdout, stderr = self._run_payload(b'{"level":"ERROR"}\r\n')
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(
            stdout, '1\t{"level":"ERROR"}' + RECORD_TERMINATOR
        )
        self.assertEqual(stderr, "")

    def test_trailing_cr_difference_is_observable_in_output(self):
        # 两个场景的输出差异必须能被观测到：无 LF 时正文中多一个 CR。
        # 该断言依赖逐字节比较，任何通用换行归一化都会让它失效。
        _, stdout_cr, _ = self._run_payload(b'{"level":"ERROR"}\r')
        _, stdout_crlf, _ = self._run_payload(b'{"level":"ERROR"}\r\n')
        self.assertEqual(
            stdout_cr, '1\t{"level":"ERROR"}\r' + RECORD_TERMINATOR
        )
        self.assertEqual(
            stdout_crlf, '1\t{"level":"ERROR"}' + RECORD_TERMINATOR
        )
        # 正文的 CR 紧邻记录终止换行之前，是两个场景的唯一差异。
        self.assertEqual(
            stdout_cr,
            stdout_crlf[: -len(RECORD_TERMINATOR)]
            + "\r"
            + RECORD_TERMINATOR,
        )


if __name__ == "__main__":
    unittest.main()
