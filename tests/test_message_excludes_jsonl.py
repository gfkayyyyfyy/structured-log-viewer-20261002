"""--message-excludes 与 --jsonl 组合使用的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
测试输入在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、demo.jsonl、cr.jsonl、网络或外部服务；产品代码、
公开命令参数、默认逐行输出、摘要模式及其他筛选行为均保持现状。

固定行为：消息子串排除按 JSON 解码后的文字匹配，JSONL 导出按原始正文
写字节。覆盖要点（标准输出、标准错误按原始字节比较，退出码分别断言）：

- 固定样例为八行合法 ERROR 对象，前四行顶层 message 依次为
  "timeout"、"timeout retry"、"Timeout"、"retry"，后四行依次没有
  message、message 为 null、message 为数字 42、只在嵌套对象中出现
  "timeout"。排除 timeout、retry 并启用 --jsonl 时，只导出原第
  3、5、6、7、8 行：每条正文后恰有一个 LF，保持原顺序，不带行号前缀、
  不重新序列化；标准错误为空，退出码为 0。
- 排除值重复或两个值调换顺序，输出字节完全相同。
- 同一组正文分别以 LF、CRLF 结束、以及末行不带换行三种文件形式，
  导出字节完全相同。
- 同一文件改用 --message-contains timeout 且 --message-excludes retry
  时，包含与排除条件取交集，只导出第 1 行。
- 四行诊断样例：第 1 行时间为 2026-10-03T10:00:00Z、消息为 keep 的
  ERROR 对象；第 2 行 timestamp 为 null、消息为 timeout；第 3 行
  not-json；第 4 行时间与第 1 行相同、消息为 timeout。以该时刻作为
  --since 起点、排除 timeout 并导出时，标准输出只有第 1 行正文加 LF；
  标准错误依次是第 2 行的“无效日志：timestamp 缺失或格式无效”和第
  3 行的“无效日志：JSON 解析失败”，各以换行结尾；第 4 行静默排除；
  退出码为 0。
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
# 固定样例的八个物理行正文（不含行分隔符），全部是合法 ERROR 对象。
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

# 三种文件形式：LF 结束、CRLF 结束、末行不带换行。
PAYLOAD_LF = ("\n".join(FIXTURE_LINES) + "\n").encode("utf-8")
PAYLOAD_CRLF = ("\r\n".join(FIXTURE_LINES) + "\r\n").encode("utf-8")
PAYLOAD_NO_FINAL_NEWLINE = "\n".join(FIXTURE_LINES).encode("utf-8")

# 排除 timeout、retry 后保留的行：第 3、5、6、7、8 行。
KEPT_INDICES = (3, 5, 6, 7, 8)
# 期望的导出字节：保留行正文各加一个 LF，按原顺序拼接。
EXPORT_BYTES = b"".join(
    (FIXTURE_LINES[i - 1] + "\n").encode("utf-8") for i in KEPT_INDICES
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
    """--message-excludes 与 --jsonl 组合：按解码后文字排除，按原文导出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(PAYLOAD_LF)
        self.path = str(path)

    def _run_excludes(self, *needles, path=None):
        """选择 ERROR、启用 --jsonl，并按给定顺序提供 --message-excludes。"""
        args = [path or self.path, "--level", "ERROR", "--jsonl"]
        for needle in needles:
            args.extend(["--message-excludes", needle])
        return run_cli_bytes(*args)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：八个物理行均为含 ERROR 级别的合法 JSON 对象，
        # 各行顶层 message 的形状与文字与用例约定一致。
        self.assertEqual(len(FIXTURE_LINES), 8)
        self.assertEqual(PAYLOAD_LF.count(b"\n"), 8)
        self.assertEqual(PAYLOAD_CRLF.count(b"\r\n"), 8)
        self.assertFalse(PAYLOAD_NO_FINAL_NEWLINE.endswith(b"\n"))
        self.assertEqual(PAYLOAD_NO_FINAL_NEWLINE.count(b"\n"), 7)
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

    def test_excluding_timeout_and_retry_exports_kept_lines(self):
        # 排除 timeout、retry：第 1、2、4 行命中至少一个排除值被剔除；
        # 第 3 行大小写不命中，第 5/6/7 行 message 缺失/null/数字，
        # 第 8 行只在嵌套对象中出现 timeout——全部保留。
        proc = self._run_excludes("timeout", "retry")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        self.assertEqual(proc.stderr, b"")
        # 每条记录恰以一个 LF 结束（共五个），不带行号制表符前缀。
        self.assertEqual(proc.stdout.count(b"\n"), 5)
        self.assertNotIn(b"\r\n", proc.stdout)
        self.assertNotIn(b"\t", proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"3\t"))
        # 正文原样保留：未重新序列化（数字 42 仍是字面 42，嵌套对象
        # 的字段顺序与紧凑分隔符不变）。
        self.assertIn(b'"message":42}\n', proc.stdout)
        self.assertIn(b'"nested":{"message":"timeout"}}\n', proc.stdout)

    def test_swapping_exclude_value_order_gives_same_output(self):
        # 先 retry 后 timeout：导出字节与先 timeout 后 retry 完全相同。
        proc = self._run_excludes("retry", "timeout")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        self.assertEqual(proc.stderr, b"")

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
                self.assertEqual(proc.stdout, EXPORT_BYTES)
                self.assertEqual(proc.stderr, b"")

    def test_newline_variants_export_identical_bytes(self):
        # 同一组正文分别以 LF、CRLF 结束、以及末行不带换行：
        # 导出字节完全相同，每条正文后都恰有一个 LF。
        for name, payload in (
            ("lf", PAYLOAD_LF),
            ("crlf", PAYLOAD_CRLF),
            ("no-final-newline", PAYLOAD_NO_FINAL_NEWLINE),
        ):
            with self.subTest(name):
                path = Path(self._tmp.name) / f"case-{name}.jsonl"
                path.write_bytes(payload)
                proc = self._run_excludes("timeout", "retry",
                                          path=str(path))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, EXPORT_BYTES)
                self.assertEqual(proc.stderr, b"")

    def test_contains_timeout_and_excludes_retry_only_first_line(self):
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
        self.assertEqual(proc.stdout, (LINE1 + "\n").encode("utf-8"))
        self.assertEqual(proc.stderr, b"")

    def test_all_messages_excluded_exports_nothing(self):
        # 合法文件中全部消息均命中排除值：标准输出与标准错误均为空，
        # 退出码为 0。
        lines = [
            '{"level":"ERROR","message":"timeout"}',
            '{"level":"ERROR","message":"retry"}',
            '{"level":"ERROR","message":"timeout retry"}',
        ]
        path = Path(self._tmp.name) / "all-excluded.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = self._run_excludes("timeout", "retry", path=str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")


class MessageExcludesJsonlDiagnosticsTests(unittest.TestCase):
    """启用 --since 时排除条件不遮蔽无效行诊断，JSONL 导出不受影响。"""

    def test_since_with_excludes_exports_and_warns_by_line(self):
        lines = [
            # 1 合法记录，时间不早于起点，消息不被排除：唯一导出。
            '{"level":"ERROR","timestamp":"' + TIMESTAMP
            + '","message":"keep"}',
            # 2 timestamp 为 null：先于排除检查产出时间戳警告并跳过。
            '{"level":"ERROR","timestamp":null,"message":"timeout"}',
            # 3 根本不是 JSON：解析失败警告。
            "not-json",
            # 4 时间合法但消息命中排除值：静默不导出、不警告。
            '{"level":"ERROR","timestamp":"' + TIMESTAMP
            + '","message":"timeout"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "diagnostics.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli_bytes(
                str(path), "--level", "ERROR",
                "--since", TIMESTAMP,
                "--message-excludes", "timeout",
                "--jsonl",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有第 1 行正文加一个 LF，不带行号前缀。
        self.assertEqual(proc.stdout, (lines[0] + "\n").encode("utf-8"))
        # 标准错误按文件行序依次是第 2 行的 timestamp 诊断和第 3 行的
        # JSON 解析诊断，均带原行号，每行一条；第 4 行静默无警告。
        self.assertEqual(
            proc.stderr,
            "第 2 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
            + RECORD_TERMINATOR
            + "第 3 行：无效日志：JSON 解析失败".encode("utf-8")
            + RECORD_TERMINATOR,
        )


if __name__ == "__main__":
    unittest.main()
