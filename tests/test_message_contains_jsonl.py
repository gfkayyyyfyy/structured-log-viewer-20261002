"""--message-contains 与 --jsonl 组合使用的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
测试输入在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令参数、
默认逐行输出、摘要模式及其他筛选行为均保持现状。

固定行为：消息子串按 JSON 解码后的文字匹配，JSONL 导出按原始正文写字节。
同一份七物理行输入同时承担两种核对：

- 第 1 行三个空格（空白行，跳过）；第 2 行 message 为“请求失败”且整条
  正文带首尾空格；第 3 行 message 为“成功”；第 4 行 message 为 null；
  第 5 行 level 为 INFO、timestamp 为 null、message 为“成功”；
  第 6 行 not-json；第 7 行 message 仍为“请求失败”，但字段顺序不同，
  且“失败”以 JSON 转义 \\u5931\\u8d25 书写。第 2、3、4、7 行 level 均为
  ERROR，timestamp 均为 2026-10-03T10:00:00Z。
- 前六行交替以 LF、CRLF 结束，第七行不带末尾换行。

执行 python -m log_viewer case.jsonl --level ERROR --message-contains 失败
--since 2026-10-03T10:00:00Z --jsonl 时：

- 退出码 0；标准输出逐字节等于第 2 行正文加一个 LF 再接第 7 行正文加
  一个 LF：首尾空格、字段顺序与 \\u5931\\u8d25 转义写法原样保留，
  不带行号前缀（第 7 行靠解码后的文字命中，其原始字节中并无“失败”）。
- 标准错误依次只有第 5 行的“无效日志：timestamp 缺失或格式无效”和
  第 6 行的“无效日志：JSON 解析失败”，沿用既有“第 N 行：”行号格式，
  每行各一条：第 5 行消息本就不匹配，其时间戳警告不被消息条件抑制。
- 把检索值改为文件中不出现的“未出现”时，标准输出为空，警告内容、
  顺序与退出码不变。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化；标准错误的记录终止换行沿用 print 的平台换行
（os.linesep，Linux 上为 LF）。JSON 文本中字面的反斜杠转义由
chr(92) 拼接得到，避免源码里出现反斜杠-u 序列。

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

# print 写到标准错误的记录终止换行，随平台，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 反斜杠字符：用来拼出 JSON 文本中字面的 \\uXXXX 转义，保证源文件自身
# 不出现会被工具改写的反斜杠-u 序列。
BACKSLASH = chr(92)

TIMESTAMP = "2026-10-03T10:00:00Z"

# ---------------------------------------------------------------------------
# 七个物理行的正文（不含行分隔符）。
# ---------------------------------------------------------------------------
# 第 1 行：三个空格，空白行，静默跳过。
LINE1 = "   "
# 第 2 行：整条正文带首尾空格；message 为“请求失败”（中文直写）。
LINE2 = (
    '  {"level":"ERROR","timestamp":"' + TIMESTAMP
    + '","message":"请求失败"}  '
)
# 第 3 行：message 为“成功”，不含检索子串。
LINE3 = (
    '{"level":"ERROR","timestamp":"' + TIMESTAMP + '","message":"成功"}'
)
# 第 4 行：message 为 null，静默不匹配。
LINE4 = (
    '{"level":"ERROR","timestamp":"' + TIMESTAMP + '","message":null}'
)
# 第 5 行：level 为 INFO、timestamp 为 null、message 为“成功”。
# 给出 --since 后 timestamp 为 null 必产生一条警告；该警告在消息条件
# 之前判定，消息不匹配不能抑制它。
LINE5 = '{"level":"INFO","timestamp":null,"message":"成功"}'
# 第 6 行：损坏 JSON。
LINE6 = "not-json"
# 第 7 行：字段顺序不同（message 在前、level 在最后），“失败”写成
# JSON 转义 （字面的反斜杠加编号）；解码后的 message 仍是
# “请求失败”。原始字节中不存在“失败”二字，命中即证明按解码后文字匹配。
LINE7 = (
    '{"message":"请求' + BACKSLASH + "u5931" + BACKSLASH + "u8d25"
    + '","timestamp":"' + TIMESTAMP + '","level":"ERROR"}'
)

# 前六行交替以 LF、CRLF 结束（奇数行 LF、偶数行 CRLF），第七行无末尾换行。
PAYLOAD = (
    LINE1 + "\n"
    + LINE2 + "\r\n"
    + LINE3 + "\n"
    + LINE4 + "\r\n"
    + LINE5 + "\n"
    + LINE6 + "\r\n"
    + LINE7
).encode("utf-8")

# 期望的标准输出：第 2 行正文 + LF，再接第 7 行正文 + LF，逐字节相等。
EXPORT_BYTES = (LINE2 + "\n" + LINE7 + "\n").encode("utf-8")

# 期望的标准错误：第 5 行时间戳警告在前，第 6 行 JSON 解析失败在后，
# 各一条，沿用既有“第 N 行：”行号格式。
WARNING_BYTES = (
    "第 5 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
    + RECORD_TERMINATOR
    + "第 6 行：无效日志：JSON 解析失败".encode("utf-8")
    + RECORD_TERMINATOR
)

# 第 7 行正文中“失败”的字面转义形式（反斜杠+u5931、反斜杠+u8d25）。
ESC_FAILURE = (BACKSLASH + "u5931" + BACKSLASH + "u8d25").encode("utf-8")


def run_cli_bytes(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖导出字节的差异。
    """
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定；
    # 应用自身的中文消息不受这些变量影响。
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class MessageContainsJsonlTests(unittest.TestCase):
    """--message-contains 与 --jsonl 组合：按解码后文字匹配，按原文导出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(PAYLOAD)
        self.path = str(path)

    def _run(self, needle):
        return run_cli_bytes(
            self.path,
            "--level", "ERROR",
            "--message-contains", needle,
            "--since", TIMESTAMP,
            "--jsonl",
        )

    def test_fixture_layout_matches_spec(self):
        # 数据自检：七个物理行，前六行交替 LF/CRLF 终止，第七行无末尾换行。
        self.assertEqual(PAYLOAD.count(b"\n"), 6)
        self.assertEqual(PAYLOAD.count(b"\r\n"), 3)
        self.assertFalse(PAYLOAD.endswith(b"\n"))
        self.assertEqual(LINE1, "   ")
        # 第 2 行正文确带首尾空格，message 为中文直写的“请求失败”。
        self.assertTrue(LINE2.startswith("  {"))
        self.assertTrue(LINE2.endswith("}  "))
        self.assertEqual(json.loads(LINE2)["message"], "请求失败")
        # 第 7 行字段顺序不同（message 在前、level 在最后），原始字节中
        # 只有字面转义、没有“失败”二字；解码后的 message 仍是“请求失败”。
        self.assertTrue(LINE7.startswith('{"message":"请求'))
        self.assertTrue(LINE7.endswith('"level":"ERROR"}'))
        self.assertNotIn("失败", LINE7)
        self.assertIn(BACKSLASH + "u5931" + BACKSLASH + "u8d25", LINE7)
        self.assertEqual(json.loads(LINE7)["message"], "请求失败")
        # 第 2、3、4、7 行 level 均为 ERROR，timestamp 均为同一时刻。
        for line in (LINE2, LINE3, LINE4, LINE7):
            record = json.loads(line)
            self.assertEqual(record["level"], "ERROR")
            self.assertEqual(record["timestamp"], TIMESTAMP)
        # 第 5 行 level 为 INFO、timestamp 为 null、message 为“成功”。
        record5 = json.loads(LINE5)
        self.assertEqual(record5["level"], "INFO")
        self.assertIsNone(record5["timestamp"])
        self.assertEqual(record5["message"], "成功")

    def test_matching_export_preserves_raw_bodies_and_warnings(self):
        proc = self._run("失败")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出逐字节等于第 2 行正文 + LF 接第 7 行正文 + LF。
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        # 第 2 行的首尾空格原样保留。
        self.assertTrue(proc.stdout.startswith(b'  {"level":"ERROR"'))
        self.assertIn('"}  \n'.encode("utf-8"), proc.stdout)
        # 第 7 行的字段顺序与 \\u5931\\u8d25 转义写法原样保留，未被解码。
        self.assertIn(ESC_FAILURE, proc.stdout)
        self.assertIn(
            '{"message":"请求'.encode("utf-8")
            + ESC_FAILURE
            + '","timestamp":"'.encode("utf-8"),
            proc.stdout,
        )
        # 每条记录恰以一个 LF 结束（共两个），不带行号制表符前缀。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r\n", proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"2\t"))
        self.assertNotIn(b"\t", proc.stdout)
        # 标准错误依次只有第 5 行时间戳警告与第 6 行解析失败警告：
        # 第 5 行消息不匹配，其时间戳警告仍照常产生。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_match_happens_on_decoded_text_not_raw_bytes(self):
        # 第 7 行原始字节中没有“失败”，命中只能来自解码后的文字；
        # 反之，把转义序列的原文（反斜杠+u5931 等 ASCII 字符）当检索值时，
        # 解码后的文字里不存在该字面序列，标准输出应为空。
        needle = BACKSLASH + "u5931" + BACKSLASH + "u8d25"
        # 自检：解码后的消息文字里确实不存在该字面序列。
        self.assertNotIn(needle, json.loads(LINE7)["message"])
        proc = self._run(needle)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_absent_needle_exports_nothing_but_warnings_unchanged(self):
        # 检索值“未出现”在文件中不存在：标准输出为空，警告内容、顺序
        # 与退出码均不变。
        proc = self._run("未出现")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, WARNING_BYTES)


if __name__ == "__main__":
    unittest.main()
