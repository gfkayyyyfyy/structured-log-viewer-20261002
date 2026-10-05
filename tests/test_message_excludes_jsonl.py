"""--message-excludes 与 --jsonl 组合使用的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
测试输入在各用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、demo.jsonl、cr.jsonl、网络或外部服务；产品代码、
公开命令参数、默认逐行输出、摘要模式及其他筛选行为均保持现状。

固定行为：排除子串按 JSON 解码后的顶层 message 文字匹配，JSONL 导出
按原始正文写字节，每条正文后恰有一个 LF，不带行号前缀、不重新序列化。

- 固定样例连续 8 行均为合法 ERROR 对象，顶层 message 依次为
  "timeout"、"timeout retry"、"Timeout"、"retry"、缺失、null、
  数字 42，以及仅在 nested 中出现 "timeout" 而顶层缺失。选择 ERROR、
  排除 timeout 与 retry 并启用 --jsonl 时，只导出原第 3、5、6、7、8 行
  的正文，保持原顺序；标准错误为空，退出码 0。重复排除值或调换两个值
  的顺序，输出字节相同。同一组正文分别采用 LF、CRLF 和末行没有换行的
  文件形式，导出结果仍相同。
- 同一文件改用 --message-contains timeout 且 --message-excludes retry：
  包含与排除取交集，只导出第 1 行正文。
- 四行诊断样例：--since 起点为 2026-10-03T10:00:00Z 时，timestamp 为
  null 的第 2 行与 not-json 的第 3 行分别产出含原行号的
  “无效日志：timestamp 缺失或格式无效”和“无效日志：JSON 解析失败”，
  按行序每行一条；命中排除值的第 4 行静默不输出也不警告；标准输出
  只有第 1 行正文加 LF，退出码 0。
- 全部消息均命中排除值的合法文件：标准输出与标准错误均为空，退出码 0。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化；标准错误的记录终止换行沿用 print 的平台换行
（os.linesep，Linux 上为 LF）。

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

TIMESTAMP = "2026-10-03T10:00:00Z"

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

# 同一组正文的三种文件形式：LF 终止、CRLF 终止、末行没有换行。
PAYLOAD_LF = ("\n".join(FIXTURE_LINES) + "\n").encode("utf-8")
PAYLOAD_CRLF = ("\r\n".join(FIXTURE_LINES) + "\r\n").encode("utf-8")
PAYLOAD_NO_FINAL_LF = "\n".join(FIXTURE_LINES).encode("utf-8")

# 选择 ERROR、排除 timeout 与 retry 后保留的行：第 3、5、6、7、8 行。
KEPT_INDICES = (3, 5, 6, 7, 8)
# 期望的标准输出：保留行正文各接一个 LF，逐字节相等，不带行号前缀。
EXPORT_BYTES = "".join(
    FIXTURE_LINES[i - 1] + "\n" for i in KEPT_INDICES
).encode("utf-8")

# 包含 timeout 且仅排除 retry 时，只导出第 1 行正文加一个 LF。
EXPORT_FIRST_LINE = (LINE1 + "\n").encode("utf-8")

# ---------------------------------------------------------------------------
# 四行诊断样例（不含行分隔符）。
# ---------------------------------------------------------------------------
DIAG_LINE1 = (
    '{"level":"ERROR","timestamp":"' + TIMESTAMP + '","message":"keep"}'
)
# timestamp 为 null：启用 --since 后先于排除检查产出时间戳警告并跳过。
DIAG_LINE2 = '{"level":"ERROR","timestamp":null,"message":"timeout"}'
# 根本不是 JSON：解析失败警告。
DIAG_LINE3 = "not-json"
# 时间合法但消息命中排除值：静默不输出、不警告。
DIAG_LINE4 = (
    '{"level":"ERROR","timestamp":"' + TIMESTAMP + '","message":"timeout"}'
)
DIAG_LINES = [DIAG_LINE1, DIAG_LINE2, DIAG_LINE3, DIAG_LINE4]
DIAG_PAYLOAD = ("\n".join(DIAG_LINES) + "\n").encode("utf-8")

# 期望的标准输出：只有第 1 行正文加一个 LF。
DIAG_EXPORT_BYTES = (DIAG_LINE1 + "\n").encode("utf-8")

# 期望的标准错误：第 2 行时间戳警告在前，第 3 行 JSON 解析失败在后，
# 各一条，沿用既有“第 N 行：”行号格式；第 4 行静默无警告。
DIAG_WARNING_BYTES = (
    "第 2 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
    + RECORD_TERMINATOR
    + "第 3 行：无效日志：JSON 解析失败".encode("utf-8")
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


class MessageExcludesJsonlTests(unittest.TestCase):
    """--message-excludes 与 --jsonl 组合：按原文导出未被排除的记录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(PAYLOAD_LF)
        self.path = str(path)

    def _run_excludes(self, *needles, path=None):
        """选择 ERROR、按给定顺序重复提供 --message-excludes 并启用 --jsonl。"""
        args = [path or self.path, "--level", "ERROR"]
        for needle in needles:
            args.extend(["--message-excludes", needle])
        args.append("--jsonl")
        return run_cli_bytes(*args)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：8 个物理行均为含 ERROR 级别的合法 JSON 对象，
        # 各行顶层 message 的形状与文字与用例约定一致。
        self.assertEqual(len(FIXTURE_LINES), 8)
        self.assertEqual(PAYLOAD_LF.count(b"\n"), 8)
        self.assertNotIn(b"\r", PAYLOAD_LF)
        self.assertEqual(PAYLOAD_CRLF.count(b"\r\n"), 8)
        self.assertEqual(PAYLOAD_NO_FINAL_LF.count(b"\n"), 7)
        self.assertFalse(PAYLOAD_NO_FINAL_LF.endswith(b"\n"))
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

    def test_excluding_timeout_and_retry_exports_kept_raw_bodies(self):
        # 排除 timeout、retry：第 1、2、4 行命中至少一个排除值被剔除；
        # 第 3 行大小写不命中，第 5/6/7 行 message 缺失/null/数字，
        # 第 8 行只在嵌套对象中出现 timeout——全部保留并按原文导出。
        proc = self._run_excludes("timeout", "retry")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出逐字节等于第 3、5、6、7、8 行正文各接一个 LF。
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        # 每条记录恰以一个 LF 结束（共五个），不带行号制表符前缀，
        # 不重新序列化：导出字节中不存在 CR，也不存在行号前缀。
        self.assertEqual(proc.stdout.count(b"\n"), 5)
        self.assertNotIn(b"\r", proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"3\t"))
        self.assertNotIn(b"\t", proc.stdout)
        self.assertEqual(proc.stderr, b"")

    def test_swapping_exclude_value_order_gives_same_bytes(self):
        # 先 retry 后 timeout：导出字节与先 timeout 后 retry 完全相同。
        proc = self._run_excludes("retry", "timeout")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        self.assertEqual(proc.stderr, b"")

    def test_duplicate_exclude_values_give_same_bytes(self):
        for needles in (
            ("timeout", "timeout", "retry"),
            ("retry", "retry", "timeout"),
            ("timeout", "retry", "timeout", "retry"),
            ("retry", "timeout", "timeout"),
        ):
            with self.subTest(needles):
                proc = self._run_excludes(*needles)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, EXPORT_BYTES)
                self.assertEqual(proc.stderr, b"")

    def test_newline_variants_give_same_export_bytes(self):
        # 同一组正文分别采用 LF、CRLF 和末行没有换行的文件形式：
        # 行分隔符不属于正文，导出结果三种形式完全相同。
        for name, payload in (
            ("lf", PAYLOAD_LF),
            ("crlf", PAYLOAD_CRLF),
            ("no-final-lf", PAYLOAD_NO_FINAL_LF),
        ):
            with self.subTest(name):
                path = Path(self._tmp.name) / ("case-" + name + ".jsonl")
                path.write_bytes(payload)
                proc = self._run_excludes("timeout", "retry",
                                          path=str(path))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, EXPORT_BYTES)
                self.assertEqual(proc.stderr, b"")

    def test_contains_timeout_and_excludes_retry_exports_first_line(self):
        # 包含条件与排除条件取交集：第 1 行含 timeout 且不含 retry，
        # 唯一导出；第 2 行同时含两者被排除；第 3 行 "Timeout" 不满足
        # 区分大小写的包含条件；其余行不含 timeout 子串。
        proc = run_cli_bytes(
            self.path, "--level", "ERROR",
            "--message-contains", "timeout",
            "--message-excludes", "retry",
            "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPORT_FIRST_LINE)
        self.assertEqual(proc.stderr, b"")


class MessageExcludesJsonlDiagnosticsTests(unittest.TestCase):
    """--jsonl 导出时排除条件不遮蔽无效行诊断，边界逐行核对。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, payload):
        path = Path(self._tmp.name) / name
        path.write_bytes(payload)
        return str(path)

    def test_since_with_excludes_exports_and_warns_by_line(self):
        path = self._write("diagnostics.jsonl", DIAG_PAYLOAD)
        proc = run_cli_bytes(
            path, "--level", "ERROR",
            "--since", TIMESTAMP,
            "--message-excludes", "timeout",
            "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有第 1 行正文加一个 LF，不带行号前缀。
        self.assertEqual(proc.stdout, DIAG_EXPORT_BYTES)
        self.assertEqual(proc.stdout.count(b"\n"), 1)
        self.assertNotIn(b"\t", proc.stdout)
        # 标准错误按文件行序依次是第 2 行的 timestamp 诊断和第 3 行的
        # JSON 解析诊断，均带原行号，每行一条；第 4 行静默无警告。
        self.assertEqual(proc.stderr, DIAG_WARNING_BYTES)

    def test_all_messages_excluded_exports_and_warns_nothing(self):
        # 合法文件中每条消息的顶层 message 均命中排除值：
        # 标准输出与标准错误均为空，退出码 0。
        lines = [
            '{"level":"ERROR","message":"timeout"}',
            '{"level":"ERROR","message":"request timeout"}',
            '{"level":"ERROR","message":"timeout retry"}',
        ]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        path = self._write("all-excluded.jsonl", payload)
        proc = run_cli_bytes(
            path, "--level", "ERROR",
            "--message-excludes", "timeout",
            "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")


if __name__ == "__main__":
    unittest.main()
