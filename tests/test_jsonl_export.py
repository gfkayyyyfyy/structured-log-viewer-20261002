"""--jsonl 原文导出与再次导入的离线回归测试。

覆盖在既有筛选语义不变的前提下，--jsonl 导出模式的字节级约定：

- 命中记录只输出原始正文与一个 LF，不带行号/制表符前缀、外层数组或摘要；
- 正文逐字节保留：UTF-8 中文、真实 U+2028/U+2029、JSON 的 \\uXXXX 转义、
  字段顺序、首尾空格与制表符、不紧邻 LF 的单独 CR 均不重新序列化；
- LF、CRLF、末行无换行三种输入导出字节完全相同；末行仅以 CR 结束时
  该 CR 留在正文中，输出随后补 LF；
- 首次导出的字节可直接作为新日志再次导入，二次导出与首次完全相同；
- 重复选择 ERROR 或调整重复选项顺序不增加记录、不改变顺序；
- 空文件与仅含 INFO 记录的文件：两条输出流均为空，退出码 0；
- --jsonl 与 --summary 互斥：读取文件之前拒绝，退出码 2，stdout 为空，
  且不出现文件读取失败；
- 不启用 --jsonl 时的行号输出与 --summary 摘要行为保持原样。

比较一律按子进程的原始字节进行（不使用 text=True），不做通用换行
归一化，以免掩盖正文中 CR、U+2028/U+2029 等字符的差异；断言失败消息
指出首个差异字节的位置及其上下文，便于分辨正文、换行、警告或退出码
中的差异。

只用 Python 3 标准库；输入文件由用例在临时目录中自行创建并自动清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from log_viewer import normalize_level

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 五行样例的物理行正文（不含行分隔符）：
# 第 1 行：合法 INFO 对象（不匹配 ERROR）。
LINE1 = '{"level":"INFO","message":"启动完成"}'
# 第 2 行：空白行（跳过，不产生警告）。
LINE2 = ""
# 第 3 行：not-json（JSON 解析失败，唯一一条警告）。
LINE3 = "not-json"
# 第 4 行：level 为带首尾空格的小写 " error "；物理行正文在 JSON 对象
# 之外带首尾空格与制表符（前置两个空格和一个制表符，后置一个制表符和
# 两个空格）；message 含中文；note 字段在 JSON 文本中以 \u00e9 转义
# 形式书写，导出时必须原样保留为反斜杠转义，不能被解码成真实字符；
# 字段顺序也必须保留。
LINE4 = (
    '  \t{"level":" error ","message":"磁盘空间不足，请重试",'
    '"note":"caf\\u00e9"}\t  '
)
# 第 5 行：message 中“甲/乙”“乙/丙”之间是真实 U+2028/U+2029
# （Python 的 \u2028 在字符串里产生真实字符，写盘即 UTF-8 字节，
# 不是 JSON 文本里的 \\u2028 转义序列）；JSON 对象之后紧跟一个
# 单独 CR 和一个制表符，它们都是正文的一部分。
LINE5 = (
    '{"level":"ERROR","message":"甲'
    + "\u2028"
    + "乙"
    + "\u2029"
    + '丙"}\r\t'
)
FIVE_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5]

LINE4_BYTES = LINE4.encode("utf-8")
LINE5_BYTES = LINE5.encode("utf-8")
# --level ERROR --jsonl 时：标准输出仅第 4、5 行原始正文，各补一个 LF。
EXPECTED_EXPORT = LINE4_BYTES + b"\n" + LINE5_BYTES + b"\n"
# 标准错误仅第 3 行的 JSON 解析失败警告。
EXPECTED_STDERR = "第 3 行：无效日志：JSON 解析失败\n".encode("utf-8")

# 第 5 行的变体：对象后只有一个单独 CR 且文件到此结束（没有 LF）。
LINE5_TRAILING_CR = (
    '{"level":"ERROR","message":"甲'
    + "\u2028"
    + "乙"
    + "\u2029"
    + '丙"}\r'
)


def run_cli_bytes(*args):
    """运行 ``python -m log_viewer``，按原始字节捕获标准输出与标准错误。

    不使用 text=True：subprocess 文本模式会启用通用换行转换，把输出里的
    CR/CRLF 翻译成 LF，掩盖正文中单独 CR 的差异。
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


def first_byte_difference(actual, expected):
    """返回首个差异字节的位置与两边上下文；完全相同（含长度）时返回 ''。"""
    for index, (actual_byte, expected_byte) in enumerate(zip(actual, expected)):
        if actual_byte != expected_byte:
            start = max(0, index - 8)
            end = index + 9
            return (
                f"首个差异在字节 {index}："
                f"期望 {expected[start:end]!r}，实际 {actual[start:end]!r}"
            )
    if len(actual) != len(expected):
        return (
            f"前缀完全相同但长度不同：期望 {len(expected)} 字节，"
            f"实际 {len(actual)} 字节"
        )
    return ""


class JsonlExportTests(unittest.TestCase):
    """--jsonl 导出：原文逐字节保留、补 LF、警告与退出码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, payload):
        path = Path(self._tmp.name) / name
        path.write_bytes(payload)
        return str(path)

    def _export(self, path, *levels):
        args = [path, "--jsonl"]
        for level in levels:
            args += ["--level", level]
        return run_cli_bytes(*args)

    def assertStreams(
        self, proc, expected_stdout, expected_stderr, expected_code, label
    ):
        """同时核对退出码、标准错误、标准输出，并给出定位差异的消息。"""
        self.assertEqual(
            proc.returncode,
            expected_code,
            f"{label}：退出码期望 {expected_code}，实际 {proc.returncode}；"
            f"标准错误为 {proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr,
            expected_stderr,
            f"{label}：标准错误（警告）与期望不一致。"
            + first_byte_difference(proc.stderr, expected_stderr),
        )
        self.assertEqual(
            proc.stdout,
            expected_stdout,
            f"{label}：导出正文或换行与期望逐字节不一致。"
            + first_byte_difference(proc.stdout, expected_stdout),
        )

    def test_fixture_self_check(self):
        # 测试数据自检：第 4、5 行是合法 JSON 对象（首尾空白与尾部
        # CR/制表符属于 JSON 空白），第 5 行含真实 U+2028/U+2029 而非
        # 反斜杠转义；第 4 行的 note 保留 JSON 文本中的 \\u00e9 转义。
        self.assertEqual(normalize_level(json.loads(LINE4)["level"]), "ERROR")
        self.assertEqual(normalize_level(json.loads(LINE5)["level"]), "ERROR")
        self.assertIn("\u2028", LINE5)
        self.assertIn("\u2029", LINE5)
        self.assertNotIn("\\u2028", LINE5)
        self.assertNotIn("\\u2029", LINE5)
        self.assertIn("caf\\u00e9", LINE4)
        # 转义在 JSON 语义上解码为 é，但 JSON 文本本身必须是反斜杠序列。
        self.assertEqual(json.loads(LINE4)["note"], "café")
        self.assertTrue(LINE5.endswith("}\r\t"))
        # 字段顺序固定为 level、message、note。
        self.assertEqual(
            list(json.loads(LINE4)), ["level", "message", "note"]
        )

    def test_error_jsonl_exports_only_raw_bodies_each_with_one_lf(self):
        path = self._write(
            "five-lf.jsonl", ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        )
        proc = self._export(path, "ERROR")
        self.assertStreams(proc, EXPECTED_EXPORT, EXPECTED_STDERR, 0, "五行样例")
        # 输出恰有两条以 LF 结束的记录：U+2028/U+2029 与行内 CR 都
        # 不充当换行，且每条记录恰好补一个 LF。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertEqual(
            proc.stdout,
            LINE4_BYTES + b"\n" + LINE5_BYTES + b"\n",
        )

    def test_export_has_no_line_prefix_array_wrapper_or_summary(self):
        path = self._write(
            "five-lf.jsonl", ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        )
        proc = self._export(path, "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 不带行号与制表符前缀。
        self.assertFalse(proc.stdout.startswith(b"4\t"))
        self.assertNotIn(b"4\t" + LINE4_BYTES[:8], proc.stdout)
        # 不加外层数组。
        self.assertFalse(proc.stdout.lstrip().startswith(b"["))
        # 不含摘要字段。
        self.assertNotIn(b"matched_count", proc.stdout)
        self.assertNotIn(b"invalid_count", proc.stdout)
        self.assertNotIn(b"by_level", proc.stdout)
        # 首字节就是第 4 行正文的首字节（空格），末字节恰为 LF。
        self.assertEqual(proc.stdout[:1], b" ")
        self.assertEqual(proc.stdout[-1:], b"\n")

    def test_export_preserves_utf8_escapes_field_order_and_whitespace(self):
        path = self._write(
            "five-lf.jsonl", ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        )
        proc = self._export(path, "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 中文按 UTF-8 原样保留。
        self.assertIn("磁盘空间不足".encode("utf-8"), proc.stdout)
        # JSON 文本中的 \u00e9 转义逐字节保留（反斜杠 + u00e9），
        # 不能被重新解码/序列化成真实 \u00e9 的 UTF-8 字节。
        self.assertIn(b"caf\\u00e9", proc.stdout)
        self.assertNotIn("café".encode("utf-8"), proc.stdout)
        # 真实 U+2028/U+2029 的 UTF-8 字节（E2 80 A8 / E2 80 A9）原样保留，
        # 不能被替换成 JSON 转义文本。
        self.assertIn("\u2028".encode("utf-8"), proc.stdout)
        self.assertIn("\u2029".encode("utf-8"), proc.stdout)
        self.assertNotIn(b"\\u2028", proc.stdout)
        self.assertNotIn(b"\\u2029", proc.stdout)
        # 正文首尾空格、制表符、字段顺序原样保留。
        self.assertTrue(proc.stdout.startswith(LINE4_BYTES[:4]))
        self.assertIn(b'"level":" error "', proc.stdout)
        self.assertIn(
            b'"message":"' + "磁盘空间不足".encode("utf-8"), proc.stdout
        )
        self.assertIn(
            b'"note":"caf\\u00e9"}\t  \n',
            proc.stdout,
        )
        # 第 5 行对象后的单独 CR 与制表符保留在补写的 LF 之前。
        self.assertIn(LINE5_BYTES + b"\n", proc.stdout)

    def test_lf_crlf_and_missing_final_newline_export_identical_bytes(self):
        payloads = {
            "LF，末行有换行": ("\n".join(FIVE_LINES) + "\n").encode("utf-8"),
            "CRLF，末行有换行": (
                "\r\n".join(FIVE_LINES) + "\r\n"
            ).encode("utf-8"),
            "LF，末行无换行": "\n".join(FIVE_LINES).encode("utf-8"),
            "CRLF，末行无换行": "\r\n".join(FIVE_LINES).encode("utf-8"),
        }
        for label, payload in payloads.items():
            with self.subTest(label):
                path = self._write(
                    "line-endings.jsonl", payload
                )
                proc = self._export(path, "ERROR")
                self.assertStreams(
                    proc, EXPECTED_EXPORT, EXPECTED_STDERR, 0, label
                )

    def test_final_line_ending_with_lone_cr_keeps_cr_then_appends_lf(self):
        # 末行（一条 ERROR）仅以单独 CR 结束、其后没有 LF：该 CR 属于
        # 正文必须保留，导出在正文之后补一个 LF（即结尾为 CR LF）。
        payload = "\n".join(
            [LINE1, LINE2, LINE3, LINE4, LINE5_TRAILING_CR]
        ).encode("utf-8")
        path = self._write("trailing-cr.jsonl", payload)
        proc = self._export(path, "ERROR")
        expected = (
            LINE4_BYTES + b"\n" + LINE5_TRAILING_CR.encode("utf-8") + b"\n"
        )
        self.assertStreams(proc, expected, EXPECTED_STDERR, 0, "末行仅以 CR 结束")
        # CR 留在正文中，补写的 LF 紧随其后；不能把 CR 当分隔符丢掉，
        # 也不能再补一个 CR。
        self.assertTrue(proc.stdout.endswith(b"}\r\n"))
        self.assertFalse(proc.stdout.endswith(b"\r\r\n"))
        # 与同一正文以 LF 结束（正文不含尾 CR）的导出相比，唯一差异
        # 就是结尾 LF 之前多出的这一个 CR。
        line5_without_cr = LINE5_TRAILING_CR[:-1].encode("utf-8")
        expected_lf = LINE4_BYTES + b"\n" + line5_without_cr + b"\n"
        self.assertEqual(
            proc.stdout,
            expected_lf[:-1] + b"\r\n",
            "末行单独 CR 必须作为正文保留，且其后恰好补一个 LF",
        )

    def test_reimporting_first_export_reproduces_identical_bytes(self):
        # 首次导出：五行样例 -> 仅第 4、5 行原文。
        source = self._write(
            "source.jsonl", ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        )
        first = self._export(source, "ERROR")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, EXPECTED_EXPORT)
        self.assertEqual(first.stderr, EXPECTED_STDERR)

        # 把首次导出的字节直接保存为新的 UTF-8 日志文件，再用同一入口、
        # 同一 ERROR 条件导出一次。
        reimported = self._write("reimported.jsonl", first.stdout)
        second = self._export(reimported, "ERROR")
        self.assertEqual(second.returncode, 0, second.stderr)
        # 第二次输出与第一次逐字节相同（导出是幂等的）。
        self.assertEqual(
            second.stdout,
            first.stdout,
            "再次导入后的导出应与首次导出逐字节相同。"
            + first_byte_difference(second.stdout, first.stdout),
        )
        # 第二次的输入只含两条合法 ERROR 记录：没有任何警告。
        self.assertEqual(second.stderr, b"")

    def test_duplicate_levels_and_option_order_do_not_duplicate_or_reorder(self):
        path = self._write(
            "five-lf.jsonl", ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        )
        variants = [
            ("单个 ERROR", [path, "--jsonl", "--level", "ERROR"]),
            ("重复 ERROR", [
                path, "--jsonl", "--level", "ERROR", "--level", "ERROR",
            ]),
            ("带空白小写的重复", [
                path, "--jsonl",
                "--level", " error ", "--level", "ERROR",
            ]),
            ("重复选项顺序对调", [
                path, "--jsonl",
                "--level", "ERROR", "--level", " error ",
            ]),
            ("--jsonl 置于级别之后", [
                path, "--level", " error ", "--level", "ERROR", "--jsonl",
            ]),
        ]
        for label, args in variants:
            with self.subTest(label):
                proc = run_cli_bytes(*args)
                self.assertStreams(
                    proc, EXPECTED_EXPORT, EXPECTED_STDERR, 0, label
                )
                # 始终恰好两条记录、按文件行序（第 4 行在第 5 行之前）。
                self.assertEqual(proc.stdout.count(b"\n"), 2)
                self.assertLess(
                    proc.stdout.find(LINE4_BYTES),
                    proc.stdout.find(LINE5_BYTES),
                )

    def test_empty_file_and_info_only_file_produce_empty_streams(self):
        cases = {
            "空文件": b"",
            "仅含一条合法 INFO": (
                '{"level":"INFO","message":"只有信息"}\n'
            ).encode("utf-8"),
            "仅含多条合法 INFO": (
                '{"level":"INFO","message":"一"}\n'
                '{"level":" info "}\n'
            ).encode("utf-8"),
        }
        for label, payload in cases.items():
            with self.subTest(label):
                path = self._write("empty-or-info.jsonl", payload)
                proc = self._export(path, "ERROR")
                self.assertStreams(proc, b"", b"", 0, label)


class JsonlSummaryConflictTests(unittest.TestCase):
    """--jsonl 与 --summary 互斥：读文件之前拒绝。"""

    def test_conflict_rejected_before_file_read(self):
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            variants = [
                [missing, "--level", "ERROR", "--jsonl", "--summary"],
                [missing, "--summary", "--jsonl", "--level", "ERROR"],
                [missing, "--level", " eRrOr ", "--summary", "--jsonl"],
            ]
            for args in variants:
                with self.subTest(args[3:]):
                    proc = run_cli_bytes(*args)
                    self.assertEqual(
                        proc.returncode,
                        2,
                        f"互斥开关应返回退出码 2，实际 {proc.returncode}",
                    )
                    self.assertEqual(
                        proc.stdout,
                        b"",
                        "参数被拒绝时标准输出必须为空",
                    )
                    stderr = proc.stderr.decode("utf-8")
                    self.assertIn("--jsonl", stderr)
                    self.assertIn("--summary", stderr)
                    self.assertIn("不能同时使用", stderr)
                    # 校验先于文件读取：不得出现文件读取失败。
                    self.assertNotIn("文件读取失败", stderr)


class NonExportBehaviorUnchangedTests(unittest.TestCase):
    """不启用 --jsonl 时：行号输出与 --summary 摘要保持原有行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "five.jsonl")
        Path(self.path).write_bytes(
            ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        )

    def test_default_line_number_output_unchanged(self):
        proc = run_cli_bytes(self.path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 仍是“原始行号<Tab>原文”，记录终止换行沿用 print 的 LF。
        expected = (
            b"4\t" + LINE4_BYTES + b"\n" + b"5\t" + LINE5_BYTES + b"\n"
        )
        self.assertEqual(
            proc.stdout,
            expected,
            "不启用 --jsonl 时行号前缀输出应保持原样。"
            + first_byte_difference(proc.stdout, expected),
        )
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_summary_output_unchanged(self):
        proc = run_cli_bytes(
            self.path, "--level", "ERROR", "--summary"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            set(summary), {"matched_count", "invalid_count", "by_level"}
        )
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(summary["by_level"]["ERROR"], 2)
        # 摘要模式不输出匹配正文。
        self.assertNotIn(LINE4_BYTES, proc.stdout)
        self.assertEqual(proc.stderr, EXPECTED_STDERR)


if __name__ == "__main__":
    unittest.main()
