"""末行单独 CR 的“导出 → 再读取”端到端回归测试。

对应 docs/export-reimport.txt 已公开的流程：先用 --jsonl 导出匹配记录，
把标准输出字节原样保存为新文件，再经同一公开入口 python -m log_viewer
以默认行号模式读取，核对行号重算与正文边界变化。本文件只新增测试，
不新增参数、不修改产品代码或既有文档；输入与中间结果均在用例自建的
临时目录中准备并自动清理，不依赖仓库样例文件（demo.jsonl、cr.jsonl
等）、网络或标准库以外的依赖。

主用例输入（UTF-8，文件开头恰有一次 BOM，共四个物理行）：
  第 1 行  {"level":"INFO","message":"开始"}    以 LF 结束
  第 2 行  （空行）                              以 CRLF 结束
  第 3 行  not-json                              以 LF 结束
  第 4 行  {"level":"ERROR","message":"失败"}    对象后只有一个 CR，
                                                文件随即结束，没有 LF

对照输入仅把第 4 行结尾改成 CRLF，其余字节完全相同。

核对要点：
- 第一次（--level ERROR --jsonl）：标准输出逐字节等于第 4 行 JSON 正文
  后接 CR、LF（主用例）或只接 LF（对照）；不含 BOM、行号、空行、警告
  或 INFO 记录；标准错误仅有“第 3 行：无效日志：JSON 解析失败”及平台
  末尾换行；退出码 0。
- 第二次（--level ERROR，默认行号模式读取保存的导出字节）：标准输出为
  行号 1、一个制表符和同一 JSON 对象，随后是平台输出换行；对象正文后
  不再保留额外 CR；标准错误为空；退出码 0。
- 正文中的 CR、导出补的 LF 与平台输出换行分别断言，全程按原始字节
  比较（不开 text=True），不做换行归一化；再次导入不被解释为恢复原
  文件（中间文件与原始输入字节不同，行号由 4 重算为 1）。

从项目根目录执行：

    python -m unittest discover -s tests

全部通过时退出码为 0；预期不符时由对应断言报告实际与期望的字节差异。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# print（默认行号模式的标准输出、警告的标准错误）在当前平台的记录
# 终止换行，按字节比较；Linux 上为 LF。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

BOM = b"\xef\xbb\xbf"

# 四个物理行的正文（不含行分隔符），中文按 UTF-8 编码。
LINE1_BYTES = '{"level":"INFO","message":"开始"}'.encode("utf-8")
LINE3_BYTES = b"not-json"
LINE4_BYTES = '{"level":"ERROR","message":"失败"}'.encode("utf-8")

# 主用例输入：开头恰一次 BOM；第 1 行 LF、第 2 行空行 CRLF、第 3 行 LF；
# 第 4 行对象后只有一个 CR，文件随即结束，没有 LF。
BARE_CR_INPUT = (
    BOM
    + LINE1_BYTES + b"\n"
    + b"\r\n"
    + LINE3_BYTES + b"\n"
    + LINE4_BYTES + b"\r"
)
# 对照输入：仅第 4 行结尾由单独 CR 改为 CRLF，其余字节完全相同。
CRLF_INPUT = BARE_CR_INPUT + b"\n"

# 第一次导出（--level ERROR --jsonl）的期望标准输出：
# 主用例中第 4 行末尾的单独 CR 是正文内容，原样写出后由导出补一个 LF；
# 对照中 CRLF 是行结束符，正文后只有导出补的 LF。
BARE_CR_EXPORT = LINE4_BYTES + b"\r\n"
CRLF_EXPORT = LINE4_BYTES + b"\n"

# 第一次运行的标准错误：仅第 3 行一条 JSON 解析失败警告（空行占行号，
# 故行号是 3 而不是 2），print 终止换行随平台。
WARNING_BYTES = (
    "第 3 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)

# 第二次运行（--level ERROR 默认行号模式）的期望标准输出：行号从 1 重新
# 计算；保存的导出字节中“正文 + CR + LF”的 CR 此时紧邻 LF，被当作 CRLF
# 行结束符的一部分去除，正文末尾不再含 CR。
SECOND_RUN_STDOUT = b"1\t" + LINE4_BYTES + RECORD_TERMINATOR


def run_cli_bytes(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖正文 CR、导出 LF 与平台换行之间的差别。
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


class ExportReimportBareCrTests(unittest.TestCase):
    """末行单独 CR：导出保留 CR 并补 LF，再读取时该 CR 作为 CRLF 被去除。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, payload: bytes, name: str) -> str:
        path = Path(self._tmp.name) / name
        path.write_bytes(payload)
        return str(path)

    def _run_export_then_reimport(self, payload: bytes, label: str):
        """第一次 --jsonl 导出，把标准输出字节原样存盘，第二次默认模式读取。"""
        first_path = self._write(payload, label + "-input.jsonl")
        first = run_cli_bytes(first_path, "--level", "ERROR", "--jsonl")
        second_path = self._write(first.stdout, label + "-exported.jsonl")
        second = run_cli_bytes(second_path, "--level", "ERROR")
        return first, second, first.stdout

    def test_fixture_has_one_bom_and_four_physical_lines(self):
        # 夹具自检：开头恰有一次 BOM；三个 LF 划分出四个物理行；
        # 末行以单独 CR 结束且其后没有 LF；对照仅多末尾一个 LF。
        self.assertTrue(BARE_CR_INPUT.startswith(BOM))
        self.assertEqual(BARE_CR_INPUT.count(BOM), 1)
        self.assertEqual(BARE_CR_INPUT.count(b"\n"), 3)
        self.assertTrue(BARE_CR_INPUT.endswith(b"\r"))
        self.assertEqual(CRLF_INPUT, BARE_CR_INPUT + b"\n")
        self.assertTrue(CRLF_INPUT.endswith(b"\r\n"))
        # 两个输入的前三个物理行完全相同，差别仅在第 4 行结尾。
        self.assertEqual(
            BARE_CR_INPUT.split(b"\n")[:3], CRLF_INPUT.split(b"\n")[:3]
        )

    def test_first_export_keeps_body_cr_then_appends_lf(self):
        first, _, exported = self._run_export_then_reimport(
            BARE_CR_INPUT, "bare-cr"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        # 标准输出逐字节等于第 4 行 JSON 正文后接 CR、LF。
        self.assertEqual(first.stdout, BARE_CR_EXPORT)
        self.assertEqual(exported, BARE_CR_EXPORT)
        # 结尾两字节可区分：0d 是正文原有的单独 CR，0a 是导出补的 LF。
        self.assertTrue(first.stdout.endswith(b"\r\n"))
        self.assertEqual(first.stdout.count(b"\n"), 1)
        self.assertEqual(first.stdout.count(b"\r"), 1)
        # 不含 BOM、行号制表符前缀、空行、警告文本或 INFO 记录。
        self.assertNotIn(BOM, first.stdout)
        self.assertNotIn(b"\t", first.stdout)
        self.assertNotIn(LINE1_BYTES, first.stdout)
        self.assertNotIn(LINE3_BYTES, first.stdout)
        # 中文与字段顺序保持原样（level 在 message 前，UTF-8 明文）。
        self.assertTrue(first.stdout.startswith(LINE4_BYTES[:20]))
        self.assertIn("失败".encode("utf-8"), first.stdout)
        # 标准错误仅有第 3 行的解析失败警告及平台末尾换行。
        self.assertEqual(first.stderr, WARNING_BYTES)

    def test_second_default_display_renumbers_and_drops_cr(self):
        first, second, exported = self._run_export_then_reimport(
            BARE_CR_INPUT, "bare-cr"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        # 第二次只显示行号 1、一个制表符和相同 JSON 对象，随后是平台
        # 输出换行；对象正文后不再保留额外 CR。
        self.assertEqual(second.stdout, SECOND_RUN_STDOUT)
        self.assertNotIn(b"\r", second.stdout)
        self.assertEqual(second.stdout.count(b"\t"), 1)
        self.assertTrue(second.stdout.startswith(b"1\t"))
        self.assertTrue(second.stdout.endswith(RECORD_TERMINATOR))
        # 标准错误为空：保存的导出文件只有一条合法 ERROR 记录。
        self.assertEqual(second.stderr, b"")
        # 再次导入不是恢复原文件：中间文件只有一行、无 BOM、行号由 4
        # 重算为 1，字节与原始输入不同。
        self.assertNotEqual(exported, BARE_CR_INPUT)
        self.assertEqual(exported.count(b"\n"), 1)
        self.assertNotIn(BOM, exported)

    def test_control_crlf_ending_exports_lf_only(self):
        first, second, exported = self._run_export_then_reimport(
            CRLF_INPUT, "crlf"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        # 对照：第 4 行以 CRLF 结束时 CR 是行结束符，导出正文后只有 LF。
        self.assertEqual(first.stdout, CRLF_EXPORT)
        self.assertEqual(exported, CRLF_EXPORT)
        self.assertNotIn(b"\r", first.stdout)
        self.assertEqual(first.stdout.count(b"\n"), 1)
        # 第一次的警告与退出码与主用例相同。
        self.assertEqual(first.stderr, WARNING_BYTES)
        # 第二次的默认显示与主用例完全相同：行号 1、制表符、同一对象、
        # 平台输出换行，正文后无 CR，标准错误为空，退出码 0。
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, SECOND_RUN_STDOUT)
        self.assertEqual(second.stderr, b"")

    def test_bare_cr_and_crlf_exports_are_observably_different(self):
        # 两种末行结尾的第一次导出必须可区分：差的一个字节是正文 CR，
        # 不能通过换行归一化掩盖；而第二次默认显示两者一致。
        first_cr, second_cr, _ = self._run_export_then_reimport(
            BARE_CR_INPUT, "bare-cr"
        )
        first_crlf, second_crlf, _ = self._run_export_then_reimport(
            CRLF_INPUT, "crlf"
        )
        self.assertNotEqual(first_cr.stdout, first_crlf.stdout)
        self.assertEqual(first_cr.stdout, first_crlf.stdout[:-1] + b"\r\n")
        self.assertEqual(second_cr.stdout, second_crlf.stdout)
        self.assertEqual(first_cr.stderr, first_crlf.stderr)
        self.assertEqual(second_cr.stderr, second_crlf.stderr)


if __name__ == "__main__":
    unittest.main()
