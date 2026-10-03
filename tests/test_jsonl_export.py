"""--jsonl 原文保留导出与再次导入的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
测试输入均在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、命令参数、
筛选语义与模块接口均保持不变。

覆盖要点：
- 五行样例（合法 INFO、空白行、not-json、两条 ERROR）启用 --jsonl 后
  标准输出仅含两条匹配记录的原始正文，每条补一个 LF；不带行号前缀、
  数组包装或统计摘要；标准错误只报告第 3 行的 JSON 解析失败；退出码 0。
- 正文逐字节保留：首尾空格与制表符、字段顺序、中文 UTF-8 字节、JSON
  Unicode 转义（反斜杠加编号的字面形式）以及真实 U+2028/U+2029 与单独 CR。
- 相同正文在 LF、CRLF、末行无换行三种输入下导出字节完全相同；
  末行仅以 CR 结尾而无 LF 时该 CR 留在正文中，输出随后补 LF。
- 首次导出的字节保存为临时日志后经同一入口、同一 ERROR 条件再次导出，
  两次字节完全相同，第二次标准错误为空，退出码 0。
- 重复选择 ERROR 或调整重复选项的顺序不增加记录、不改变顺序。
- 空文件与仅含合法 INFO 记录的文件导出 ERROR 时两条输出流均空，退出码 0。
- --jsonl 与 --summary 同时出现（路径不存在、级别合法）：退出码 2、
  标准输出为空、标准错误说明两个开关不能同时使用，且不出现文件读取失败。
- 不启用导出时的行号输出与 --summary 摘要行为继续保持原样。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化，以免掩盖正文中 CR 的变化。导出模式直接写字节流，
记录终止符固定为 LF（与运行平台无关）；行号模式与标准错误沿用 print 的
平台终止换行（os.linesep），在 Linux 上同为 LF。

为避免源码里出现不可见的行/段分隔字符，所有特殊字符都以 chr(码点)
构造：真实 U+2028/U+2029 由 chr(0x2028)/chr(0x2029) 在运行时产生，
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

# print（行号模式、标准错误警告）在当前平台的记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 真实的 U+2028 LINE SEPARATOR 与 U+2029 PARAGRAPH SEPARATOR：
# 运行时才产生的真实字符，写盘即 UTF-8 字节；它们是 JSON 字符串值内的
# 真实字符，不是 JSON 文本里“反斜杠+u+四位编号”的转义序列。
LS = chr(0x2028)
PS = chr(0x2029)
# 反斜杠字符：用来拼出 JSON 文本中字面的反斜杠转义，保证源文件自身
# 不出现会被工具改写的反斜杠-u 序列。
BACKSLASH = chr(92)

# ---------------------------------------------------------------------------
# 五行样例的物理正文（不含行分隔符）：
# 第 1 行合法 INFO（不匹配）；第 2 行空白；第 3 行 not-json（解析失败警告）；
# 第 4、5 行为两条 ERROR（规范化前 level 带首尾空白、小写）。
# ---------------------------------------------------------------------------
LINE1 = '{"level":"INFO","message":"启动"}'
LINE2 = "   "
LINE3 = "not-json"
# 第 4 行：整条正文首尾各带空格与制表符；level 原文是带首尾空白的小写
# error；message 含中文；code 的值以 JSON 的 Unicode 转义书写（字面的
# 反斜杠加 u0041、反斜杠加 u0042），导出时该转义必须逐字节保留，
# 不能被解码成 "AB"。
LINE4 = (
    '  \t{"level":" error ","message":"中文 失败","code":"'
    + BACKSLASH + "u0041" + BACKSLASH + "u0042"
    + '"}\t  '
)
# 第 5 行：message 中“二”“段”之间是真实 U+2028，“段”“落”之间是真实
# U+2029；JSON 对象之后紧跟一个单独 CR，再接一个制表符。
LINE5 = '{"level":"ERROR","message":"二' + LS + "段" + PS + '落"}\r\t'
FIVE_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5]

# 启用 --jsonl 且选择 ERROR 时的标准输出：仅第 4、5 行原始正文，各补一个 LF。
EXPORT_BYTES = (LINE4 + "\n" + LINE5 + "\n").encode("utf-8")
# 标准错误：仅第 3 行一条 JSON 解析失败警告（print 终止换行随平台）。
WARNING_BYTES = (
    "第 3 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)

# JSON 文本中字面的反斜杠转义片段，便于按字节断言“未被解码”。
ESC_2028 = (BACKSLASH + "u2028").encode("utf-8")
ESC_2029 = (BACKSLASH + "u2029").encode("utf-8")
ESC_CODE = (BACKSLASH + "u0041" + BACKSLASH + "u0042").encode("utf-8")


def run_cli_bytes(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖正文中 CR 的差异。
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


class JsonlExportTests(unittest.TestCase):
    """--jsonl 导出：只写原始正文 + LF，警告仍走标准错误。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, payload: bytes, name="case.jsonl") -> str:
        path = Path(self._tmp.name) / name
        path.write_bytes(payload)
        return str(path)

    def _run(self, payload: bytes, *extra, name="case.jsonl"):
        return run_cli_bytes(self._write(payload, name), *extra)

    def test_fixture_contains_tricky_bytes_not_json_escapes(self):
        # 数据自检：第 5 行含真实 U+2028/U+2029（写盘为 UTF-8 字节），
        # 而非“反斜杠+u2028/u2029”的 JSON 转义文本。
        self.assertIn(LS, LINE5)
        self.assertIn(PS, LINE5)
        self.assertNotIn(BACKSLASH + "u2028", LINE5)
        self.assertNotIn(BACKSLASH + "u2029", LINE5)
        # 真实分隔符的 UTF-8 编码分别是 E2 80 A8 / E2 80 A9。
        self.assertIn(LS.encode("utf-8"), EXPORT_BYTES)
        self.assertIn(PS.encode("utf-8"), EXPORT_BYTES)
        self.assertNotIn(ESC_2028, EXPORT_BYTES)
        self.assertNotIn(ESC_2029, EXPORT_BYTES)
        # 第 4 行的反斜杠加 u0041/u0042 是合法 JSON 转义（解码为 "AB"），
        # 但导出正文中必须保留为字面反斜杠形式。
        self.assertEqual(json.loads(LINE4)["code"], "AB")
        self.assertIn(ESC_CODE, EXPORT_BYTES)
        # 第 4 行整条正文首尾确有空格与制表符；level 原文带空白且小写。
        self.assertTrue(LINE4.startswith("  \t"))
        self.assertTrue(LINE4.endswith("\t  "))
        self.assertIn('"level":" error "', LINE4)
        # 第 5 行对象之后是单独 CR 再接制表符。
        self.assertTrue(LINE5.endswith('}\r\t'))
        # 中文明文以 UTF-8 字节出现在导出中，字段顺序保持 level/message/code。
        self.assertIn("失败".encode("utf-8"), EXPORT_BYTES)
        self.assertIn(
            '"level":" error ","message":"中文 失败","code"'.encode("utf-8"),
            EXPORT_BYTES,
        )

    def test_five_line_fixture_exports_only_two_raw_bodies_with_lf(self):
        proc = self._run(
            ("\n".join(FIVE_LINES) + "\n").encode("utf-8"),
            "--level", "ERROR", "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出逐字节等于第 4、5 行原文各补一个 LF。
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        # 每条记录恰以一个 LF 结束（共两个），无 CRLF、无多余结尾。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r\n", proc.stdout)
        # 不带行号制表符前缀，不加外层数组或统计字段。
        self.assertFalse(proc.stdout.startswith(b"4\t"))
        self.assertFalse(proc.stdout.startswith(b"["))
        self.assertNotIn(b"matched_count", proc.stdout)
        # 标准错误仅有第 3 行的 JSON 解析失败警告。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_lf_crlf_and_missing_final_newline_export_identical_bytes(self):
        payloads = {
            "LF，末行有换行": ("\n".join(FIVE_LINES) + "\n").encode("utf-8"),
            "CRLF，末行有换行": ("\r\n".join(FIVE_LINES) + "\r\n").encode("utf-8"),
            "末行无换行符": "\n".join(FIVE_LINES).encode("utf-8"),
        }
        for label, payload in payloads.items():
            with self.subTest(label):
                proc = self._run(
                    payload, "--level", "ERROR", "--jsonl",
                    name="case-" + label.replace("，", "_") + ".jsonl",
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                # 三种输入的导出字节必须完全一致。
                self.assertEqual(proc.stdout, EXPORT_BYTES)
                self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_final_line_ending_with_bare_cr_keeps_cr_then_appends_lf(self):
        # 末行（匹配 ERROR）仅以单独 CR 结尾、没有 LF：该 CR 不是行分隔符，
        # 必须留在正文中，导出随后补一个 LF，即正文 CR + 补充 LF。
        body = '{"level":"ERROR","message":"仅 CR 结尾"}'
        proc = self._run(
            ('{"level":"INFO"}\n' + body + "\r").encode("utf-8"),
            "--level", "ERROR", "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, body.encode("utf-8") + b"\r\n")
        self.assertEqual(proc.stderr, b"")
        # 对照：同样正文用 CRLF 终止时，作为分隔符的 CR 被移除，再补 LF，
        # 导出中正文后只有一个 LF——两个场景的差异必须可观测。
        proc_crlf = self._run(
            ('{"level":"INFO"}\r\n' + body + "\r\n").encode("utf-8"),
            "--level", "ERROR", "--jsonl",
            name="crlf.jsonl",
        )
        self.assertEqual(proc_crlf.returncode, 0, proc_crlf.stderr)
        self.assertEqual(proc_crlf.stdout, body.encode("utf-8") + b"\n")
        self.assertNotEqual(proc.stdout, proc_crlf.stdout)
        self.assertEqual(proc.stdout, proc_crlf.stdout[:-1] + b"\r\n")

    def test_exported_bytes_round_trip_through_same_entry_point(self):
        # 首次导出，把得到的字节原样保存为新的临时日志。
        first_path = self._write(
            ("\n".join(FIVE_LINES) + "\n").encode("utf-8"), "first.jsonl"
        )
        first = run_cli_bytes(first_path, "--level", "ERROR", "--jsonl")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, EXPORT_BYTES)
        second_path = self._write(first.stdout, "second.jsonl")
        # 再次以同一入口、同一 ERROR 条件导出：结果与首次完全相同；
        # 导出文件每行都以 LF 结束，没有坏行，故标准错误为空。
        second = run_cli_bytes(second_path, "--level", "ERROR", "--jsonl")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(second.stdout, EXPORT_BYTES)
        self.assertEqual(second.stderr, b"")

    def test_duplicate_levels_and_option_order_keep_two_records(self):
        arg_variants = [
            ("--level", "ERROR", "--level", "ERROR", "--jsonl"),
            ("--level", " error ", "--level", "ERROR", "--jsonl"),
            ("--level", "ERROR", "--level", " error ", "--jsonl"),
            ("--level", " error ", "--level", " error ", "--jsonl"),
            # --jsonl 插在重复的 --level 之间也不应改变结果。
            ("--level", "ERROR", "--jsonl", "--level", "ERROR"),
        ]
        payload = ("\n".join(FIVE_LINES) + "\n").encode("utf-8")
        for extra in arg_variants:
            with self.subTest(extra):
                proc = self._run(payload, *extra)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, EXPORT_BYTES)
                self.assertEqual(proc.stderr, WARNING_BYTES)
                # 始终只有两条记录、保持原文件行序（第 4 行在第 5 行之前）。
                self.assertEqual(proc.stdout.count(b"\n"), 2)
                self.assertLess(
                    proc.stdout.find(LINE4.encode("utf-8")),
                    proc.stdout.find(LINE5.encode("utf-8")),
                )

    def test_empty_file_and_info_only_file_export_nothing(self):
        for label, payload in (
            ("空文件", b""),
            ("仅含合法 INFO",
             '{"level":"INFO","message":"启动"}\n'.encode("utf-8")),
        ):
            with self.subTest(label):
                proc = self._run(
                    payload, "--level", "ERROR", "--jsonl",
                    name="edge-" + label + ".jsonl",
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, b"")
                self.assertEqual(proc.stderr, b"")


class JsonlSummaryConflictTests(unittest.TestCase):
    """--jsonl 与 --summary 互斥：读取文件之前拒绝。"""

    def test_both_flags_rejected_before_reading_nonexistent_file(self):
        # 路径刻意不存在、级别刻意合法：只能因开关互斥而失败，
        # 不能先去读文件，因此标准错误不得出现文件读取失败。
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            for order in (
                ("--level", "ERROR", "--jsonl", "--summary"),
                ("--level", "ERROR", "--summary", "--jsonl"),
            ):
                with self.subTest(order):
                    proc = run_cli_bytes(missing, *order)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, b"")
                    stderr = proc.stderr.decode("utf-8")
                    self.assertIn("参数错误", stderr)
                    self.assertIn("--jsonl", stderr)
                    self.assertIn("--summary", stderr)
                    self.assertIn("不能同时使用", stderr)
                    self.assertNotIn("文件读取失败", stderr)


class NonExportBehaviorUnchangedTests(unittest.TestCase):
    """不启用 --jsonl 时，行号输出与 --summary 摘要行为保持原样。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, payload: bytes) -> str:
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(payload)
        return str(path)

    def test_line_number_output_kept_without_jsonl_flag(self):
        path = self._write(("\n".join(FIVE_LINES) + "\n").encode("utf-8"))
        proc = run_cli_bytes(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 仍是“原始行号<Tab>原文”，第 4、5 行各自带平台记录终止换行；
        # 第 5 行正文末尾的单独 CR、制表符都在记录终止换行之前保留。
        expected = (
            ("4\t" + LINE4).encode("utf-8") + RECORD_TERMINATOR
            + ("5\t" + LINE5).encode("utf-8") + RECORD_TERMINATOR
        )
        self.assertEqual(proc.stdout, expected)
        self.assertTrue(proc.stdout.startswith(b"4\t"))
        # 与 --jsonl 导出不同：行号前缀存在。
        self.assertNotEqual(proc.stdout, EXPORT_BYTES)
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_summary_mode_kept_without_jsonl_flag(self):
        path = self._write(("\n".join(FIVE_LINES) + "\n").encode("utf-8"))
        proc = run_cli_bytes(path, "--level", "ERROR", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出仍只是一个 JSON 摘要对象，不出现任何匹配原文。
        obj = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            set(obj), {"matched_count", "invalid_count", "by_level"}
        )
        self.assertEqual(obj["matched_count"], 2)
        self.assertEqual(obj["invalid_count"], 1)
        self.assertEqual(obj["by_level"]["ERROR"], 2)
        self.assertNotIn(LINE4, proc.stdout.decode("utf-8"))
        self.assertNotIn(b"\t", proc.stdout)
        self.assertEqual(proc.stderr, WARNING_BYTES)


if __name__ == "__main__":
    unittest.main()
