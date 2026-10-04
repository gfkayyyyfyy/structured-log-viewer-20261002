"""--message-contains 与 --jsonl 组合使用的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
测试输入由用例在临时目录中以 UTF-8 自行生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令参数、
默认逐行输出、--summary 摘要模式及其他筛选行为均保持现状。

固定行为（按解码后的消息匹配、按原始正文导出）：
- 七物理行样例同时含合法记录、空白行、timestamp 为 null 的记录与损坏
  JSON；前六行交替以 LF、CRLF 结束，第七行不带末尾换行。
- 检索值“失败”按 JSON 解码后的文字匹配：第七行把“失败”写成字面
  \\u5931\\u8d25 转义，与第二行的直写中文同样命中。
- 导出逐字节保留原始正文：第二行的首尾空格、第七行的字段顺序与转义
  写法都不重新序列化，各补一个 LF，不带行号前缀。
- 消息不匹配不能抑制时间戳警告：第五行 timestamp 为 null 且消息也不
  匹配，仍产生一条含原始行号的 timestamp 警告；第六行损坏 JSON 一条
  解析失败警告；标准错误按行序各一条，退出码 0。
- 检索值改为未出现的子串时标准输出为空，警告内容、顺序与退出码不变。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化。为避免源码里出现会被工具改写的反斜杠-u 序列，
JSON 文本中字面的反斜杠转义由 chr(92) 拼接得到。

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

# print（标准错误警告）在当前平台的记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 反斜杠字符：用来拼出 JSON 文本中字面的反斜杠转义，保证源文件自身
# 不出现会被工具改写的反斜杠-u 序列。
BACKSLASH = chr(92)
# 第七行 message 中“失败”二字的 JSON Unicode 转义写法（字面的
# 反斜杠+u+四位编号，共十二个 ASCII 字符），解码后与直写中文相同。
ESC_FAIL = BACKSLASH + "u5931" + BACKSLASH + "u8d25"

# ---------------------------------------------------------------------------
# 七个物理行的正文（不含行分隔符）。行号在样例中固定，断言显式写出预期
# 行号，不随行序变化隐式推导。
# ---------------------------------------------------------------------------
# 第 1 行：三个空格，空白行，静默跳过、无警告。
LINE1 = "   "
# 第 2 行：ERROR，message 为直写中文“请求失败”，整条正文带首尾空格。
LINE2 = (
    '  {"level":"ERROR","timestamp":"2026-10-03T10:00:00Z",'
    '"message":"请求失败"}  '
)
# 第 3 行：ERROR，message 为“成功”，不含检索子串。
LINE3 = (
    '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z",'
    '"message":"成功"}'
)
# 第 4 行：ERROR，message 为 null，静默不匹配。
LINE4 = (
    '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z",'
    '"message":null}'
)
# 第 5 行：INFO、timestamp 为 null、message 为“成功”（消息也不匹配）：
# 时间检查先于级别与消息条件，仍产生一条 timestamp 警告。
LINE5 = '{"level":"INFO","timestamp":null,"message":"成功"}'
# 第 6 行：损坏 JSON，一条解析失败警告。
LINE6 = "not-json"
# 第 7 行：与第 2 行同为 ERROR 且消息解码后同为“请求失败”，但字段顺序
# 不同（message 在前、level 在最后），且“失败”写作字面 \u5931\u8d25 转义。
LINE7 = (
    '{"message":"请求' + ESC_FAIL + '",'
    '"timestamp":"2026-10-03T10:00:00Z","level":"ERROR"}'
)
SEVEN_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7]

# 前六行交替以 LF、CRLF 结束，第七行不带末尾换行。
PAYLOAD = (
    LINE1 + "\n"
    + LINE2 + "\r\n"
    + LINE3 + "\n"
    + LINE4 + "\r\n"
    + LINE5 + "\n"
    + LINE6 + "\r\n"
    + LINE7
).encode("utf-8")

# 启用 --jsonl 且检索“失败”时的标准输出：第 2、7 行原始正文各补一个 LF，
# 首尾空格、字段顺序与转义写法逐字节保留，不带行号前缀。
EXPORT_BYTES = (LINE2 + "\n" + LINE7 + "\n").encode("utf-8")

# 标准错误：第 5 行 timestamp 警告在前，第 6 行解析失败警告在后，各一条。
WARNING_BYTES = (
    "第 5 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
    + RECORD_TERMINATOR
    + "第 6 行：无效日志：JSON 解析失败".encode("utf-8")
    + RECORD_TERMINATOR
)


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


class MessageContainsJsonlExportTests(unittest.TestCase):
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
            "--since", "2026-10-03T10:00:00Z",
            "--jsonl",
        )

    def test_fixture_layout_matches_spec(self):
        # 数据自检：七个物理行，行号约定与行尾结构成立。
        self.assertEqual(len(SEVEN_LINES), 7)
        self.assertEqual(LINE1, "   ")
        self.assertEqual(LINE6, "not-json")
        # 前六行交替 LF、CRLF，第七行无末尾换行：全文恰六个 LF。
        self.assertEqual(PAYLOAD.count(b"\n"), 6)
        self.assertEqual(PAYLOAD.count(b"\r\n"), 3)
        self.assertFalse(PAYLOAD.endswith(b"\n"))
        self.assertTrue(PAYLOAD.endswith(LINE7.encode("utf-8")))
        # 第 2 行正文确带首尾空格；第 7 行字段顺序不同且不含直写“失败”，
        # 只有字面的反斜杠转义写法。
        self.assertTrue(LINE2.startswith("  "))
        self.assertTrue(LINE2.endswith("  "))
        self.assertNotIn("失败", LINE7)
        self.assertIn(ESC_FAIL, LINE7)
        self.assertLess(LINE7.index('"message"'), LINE7.index('"level"'))
        # 两行解码后的 message 相同：匹配发生在解码后的文字上。
        self.assertEqual(json.loads(LINE2)["message"], "请求失败")
        self.assertEqual(json.loads(LINE7)["message"], "请求失败")
        # 第 5 行 timestamp 确为 null。
        self.assertIsNone(json.loads(LINE5)["timestamp"])

    def test_match_decoded_message_and_export_raw_body(self):
        proc = self._run("失败")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出逐字节等于第 2 行正文 + LF、第 7 行正文 + LF：
        # 首尾空格、字段顺序与 \u5931\u8d25 转义写法都保留，不重新序列化。
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        # 每条记录恰以一个 LF 结束（共两个），无 CRLF、无多余结尾。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r\n", proc.stdout)
        # 不带行号制表符前缀，不加外层数组或统计字段。
        self.assertFalse(proc.stdout.startswith(b"2\t"))
        self.assertFalse(proc.stdout.startswith(b"["))
        self.assertNotIn(b"matched_count", proc.stdout)
        # 导出中保留的是转义写法的原文，而非解码后的直写中文。
        self.assertIn(ESC_FAIL.encode("utf-8"), proc.stdout)
        # 标准错误依次只有第 5 行 timestamp 警告与第 6 行解析失败警告，
        # 消息不匹配（第 5 行）不能抑制时间戳警告。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_absent_needle_exports_nothing_but_warnings_unchanged(self):
        proc = self._run("未出现")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 检索值未出现：标准输出为空。
        self.assertEqual(proc.stdout, b"")
        # 警告内容、顺序与退出码不变。
        self.assertEqual(proc.stderr, WARNING_BYTES)


if __name__ == "__main__":
    unittest.main()
