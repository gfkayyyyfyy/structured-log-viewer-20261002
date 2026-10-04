"""末行单独 CR 的“--jsonl 导出 → 同一入口再次读取”端到端回归测试。

只依赖 Python 3 标准库（unittest / subprocess / tempfile / os），输入与
中间结果全部由用例在自建临时目录中按字节准备并在结束时清理，不依赖
demo.jsonl、sample.jsonl、cr.jsonl、result.jsonl、网络或额外依赖。

本文件只补充测试：不新增命令参数，不修改产品代码、既有文档、筛选语义、
三种输出模式（默认逐行 / --summary / --jsonl）或模块返回约定。

被测路径与 docs/export-reimport.txt 公开说明一致，测试夹具即该文第 2 节
的四物理行形状，文件开头恰有一次 UTF-8 BOM：

  第 1 行  {"level":"INFO","message":"开始"}   以 LF 结束
  第 2 行  （空行）                              以 CRLF 结束
  第 3 行  not-json                             以 LF 结束
  第 4 行  {"level":"ERROR","message":"失败"}   后接一个单独 CR，
                                                文件随即结束，没有 LF

另备一份仅把第 4 行结尾改成 CRLF 的对照输入（其余字节完全相同）。

两组输入各跑两次 ``python -m log_viewer``：

  第一次（导出）：--level ERROR --jsonl
    末行单独 CR 版：标准输出逐字节等于第 4 行 JSON 正文 + CR + LF
      （CR 是无 LF 终止的末行正文，LF 是导出固定补的终止符）；
    CRLF 对照版：标准输出逐字节等于同一 JSON 正文 + LF（CRLF 中的 CR
      作为行结束符在分行时去除）；
    两版标准错误都只有“第 3 行：无效日志：JSON 解析失败”加当前平台
    末尾换行（空行仍占第 2 行号，故坏行号为 3），退出码 0；标准输出中
    不含 BOM、行号、空行、警告文本或 INFO 记录，中文与字段顺序原样。

  第二次（再次读取）：把第一次捕获的标准输出原样写为另一份临时日志，
    以 --level ERROR 默认模式显示：
    两版结果相同——“1<Tab>同一 JSON 正文”加当前平台输出换行；行号相对
    新文件从 1 重算（与原文件第 4 行无关）；第一次导出时正文里的单独 CR
    此时紧邻导出补上的 LF，被当作 CRLF 行结束符去除，正文与平台换行之间
    不再有额外 CR；标准错误为空，退出码 0。

字节比较一律在子进程层面按原始字节进行（不开 text=True），不做任何通用
换行归一化：正文中的 CR（b"\\r"）、--jsonl 导出固定写入的 LF（b"\\n"，
与平台无关）与 print 在默认模式/标准错误使用的平台输出换行
（os.linesep）分别断言。再次读取不被视为恢复原文件：直接以默认模式读取
原文件得到的是第 4 行、正文末保留 CR 且标准错误带第 3 行警告，与再次
读取的结果逐字节不同。

从项目根目录执行：

    python -m unittest discover -s tests

全部通过时退出码为 0；预期不符时 unittest 报告具体字节差异。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 文件开头恰有一次的 UTF-8 BOM。
BOM = b"\xef\xbb\xbf"

# 四条物理行的正文（中文按 UTF-8 编码，字段顺序固定）。
LINE1 = '{"level":"INFO","message":"开始"}'.encode("utf-8")
LINE3 = b"not-json"
LINE4 = '{"level":"ERROR","message":"失败"}'.encode("utf-8")

# 主输入：BOM 一次；第 1 行 LF；第 2 行为空行、CRLF；第 3 行 LF；
# 第 4 行对象后只有一个单独 CR，文件随即结束（无 LF）。
BARE_CR_INPUT = (
    BOM + LINE1 + b"\n" + b"\r\n" + LINE3 + b"\n" + LINE4 + b"\r"
)
# 对照输入：仅把第 4 行结尾由“单独 CR”改为 CRLF，即主输入末尾再补一个 LF，
# 其余字节完全相同。
CRLF_CONTROL_INPUT = BARE_CR_INPUT + b"\n"

# --jsonl 导出直接写字节流，每条记录固定补一个 LF，与运行平台无关。
EXPORT_LF = b"\n"
# 默认模式与标准错误经 print 输出，末尾换行随当前平台。
PLATFORM_NEWLINE = os.linesep.encode("utf-8")

# 第一次导出的逐字节期望：末行单独 CR 保留在正文中，随后是导出补的 LF。
BARE_CR_EXPORT_STDOUT = LINE4 + b"\r" + EXPORT_LF
# 对照版：CRLF 中的 CR 已作为行结束符去除，正文后只有导出补的 LF。
CRLF_CONTROL_EXPORT_STDOUT = LINE4 + EXPORT_LF

# 第一次导出的标准错误：仅第 3 行一条 JSON 解析失败警告（空行占第 2 行号）。
FIRST_STAGE_STDERR = (
    "第 3 行：无效日志：JSON 解析失败".encode("utf-8") + PLATFORM_NEWLINE
)
# 第二次默认显示的逐字节期望：新文件行号 1、一个制表符、同一正文，
# 随后是当前平台输出换行。
SECOND_STAGE_STDOUT = b"1\t" + LINE4 + PLATFORM_NEWLINE


def run_cli_bytes(path, *extra):
    """在项目根目录运行 ``python -m log_viewer``，按原始字节捕获两个流。

    不使用 text=True：文本模式会启用通用换行转换，把 CR/CRLF 归一成 LF，
    掩盖正文中 CR 的差异。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", str(path), *extra],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class FinalBareCrExportReimportTests(unittest.TestCase):
    """末行单独 CR：导出字节、再次读取的行号重算与正文边界变化。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._dir = Path(self._tmp.name)

    def _write(self, payload, name):
        path = self._dir / name
        path.write_bytes(payload)
        return path

    def _export_then_reimport(self, payload, name):
        """两次运行同一公开入口：先 --jsonl 导出，再把捕获字节另存后默认显示。

        返回 (第一次进程结果, 导出字节另存的路径, 第二次进程结果)。
        """
        source = self._write(payload, name + "-source.jsonl")
        first = run_cli_bytes(source, "--level", "ERROR", "--jsonl")
        # 把子进程实际标准输出原样落盘（二进制写，不转码、不归一化换行）。
        exported = self._write(first.stdout, name + "-exported.jsonl")
        # 另存后的文件字节必须与捕获到的标准输出逐字节一致。
        self.assertEqual(exported.read_bytes(), first.stdout)
        second = run_cli_bytes(exported, "--level", "ERROR")
        return first, exported, second

    def test_fixture_shapes_are_byte_exact(self):
        """夹具自检：BOM 一次、四物理行、两版仅差末行结尾。"""
        data = BARE_CR_INPUT
        self.assertTrue(data.startswith(BOM))
        self.assertFalse(data.startswith(BOM + BOM))
        self.assertEqual(data.count(BOM), 1)
        # 三个 LF 终止前三行；末行只有 CR、没有 LF。
        self.assertEqual(data.count(b"\n"), 3)
        self.assertTrue(data.endswith(LINE4 + b"\r"))
        self.assertFalse(data.endswith(b"\n"))
        # 按 LF 划分恰为四个物理行：第 2 行分段只剩 CRLF 的 CR（去 CR 后为空行），
        # 第 4 行分段末尾保留单独 CR。
        parts = data.split(b"\n")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], BOM + LINE1)
        self.assertEqual(parts[1], b"\r")
        self.assertEqual(parts[2], LINE3)
        self.assertEqual(parts[3], LINE4 + b"\r")

        control = CRLF_CONTROL_INPUT
        # 对照版相对主输入只在末尾多一个 LF：第 4 行由此变成 CRLF 终止。
        self.assertEqual(control, data + b"\n")
        self.assertTrue(control.endswith(LINE4 + b"\r\n"))
        # 两版前三行（含开头 BOM）逐字节相同。
        self.assertEqual(control[: data.index(LINE4)], data[: data.index(LINE4)])

    def test_bare_cr_export_keeps_body_cr_then_appends_lf(self):
        """第一次导出：正文 + 正文里的单独 CR + 导出固定 LF，警告带第 3 行号。"""
        first, _exported, second = self._export_then_reimport(
            BARE_CR_INPUT, "bare-cr"
        )
        # 退出码与标准错误先断言，失败时附带捕获到的错误字节便于定位。
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stderr, FIRST_STAGE_STDERR)
        # 标准输出逐字节等于第四行 JSON 正文后接 CR、LF。
        self.assertEqual(first.stdout, BARE_CR_EXPORT_STDOUT)
        self.assertEqual(
            first.stdout,
            LINE4 + b"\r" + b"\n",
        )
        # 结构区分：正文不含 CR；恰有一个 CR，位置就在正文之后；
        # 最后一个字节是导出补的 LF（而不是平台换行转换的结果）。
        self.assertNotIn(b"\r", LINE4)
        self.assertEqual(first.stdout.count(b"\r"), 1)
        self.assertEqual(first.stdout.index(b"\r"), len(LINE4))
        self.assertEqual(first.stdout.count(b"\n"), 1)
        self.assertTrue(first.stdout.endswith(b"\r\n"))
        # 不含 BOM、行号制表符前缀、空行、警告文本或 INFO 记录。
        self.assertNotIn(BOM, first.stdout)
        self.assertNotIn(b"\t", first.stdout)
        self.assertNotIn(b"INFO", first.stdout)
        self.assertNotIn("无效日志".encode("utf-8"), first.stdout)
        self.assertNotIn(b"\n\n", first.stdout)
        # 中文明文与字段顺序（level 先于 message）逐字节保持原样。
        self.assertIn("失败".encode("utf-8"), first.stdout)
        self.assertIn(
            b'{"level":"ERROR","message":"', first.stdout
        )

        # 第二次默认显示：行号重算为 1，正文末尾不再保留额外 CR。
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stderr, b"")
        self.assertEqual(second.stdout, SECOND_STAGE_STDOUT)
        self.assertTrue(second.stdout.startswith(b"1\t"))
        # 去掉平台输出换行后恰为“1<Tab>正文”，正文与平台换行之间无额外 CR。
        display_body = second.stdout[: -len(PLATFORM_NEWLINE)]
        self.assertEqual(display_body, b"1\t" + LINE4)
        self.assertFalse(display_body.endswith(b"\r"))

    def test_crlf_control_export_has_lf_only(self):
        """对照版第一次导出：CRLF 的 CR 被当作行结束符去除，正文后只有 LF。"""
        first, _exported, second = self._export_then_reimport(
            CRLF_CONTROL_INPUT, "crlf-control"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stderr, FIRST_STAGE_STDERR)
        self.assertEqual(first.stdout, CRLF_CONTROL_EXPORT_STDOUT)
        # 导出字节中没有任何 CR；唯一 LF 是导出终止符。
        self.assertNotIn(b"\r", first.stdout)
        self.assertEqual(first.stdout.count(b"\n"), 1)
        self.assertTrue(first.stdout.endswith(LINE4 + b"\n"))
        self.assertNotIn(BOM, first.stdout)
        self.assertNotIn(b"\t", first.stdout)

        # 第二次默认显示与主输入版本完全相同，标准错误为空，退出码 0。
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stderr, b"")
        self.assertEqual(second.stdout, SECOND_STAGE_STDOUT)

    def test_first_exports_differ_but_second_displays_are_identical(self):
        """两版第一次导出必须可观测地不同，第二次默认显示却逐字节相同。"""
        first_cr, _exp_cr, second_cr = self._export_then_reimport(
            BARE_CR_INPUT, "bare-cr-pair"
        )
        first_crlf, _exp_lf, second_crlf = self._export_then_reimport(
            CRLF_CONTROL_INPUT, "crlf-pair"
        )
        # 第一次导出仅差一个 CR：单独 CR 版为 正文+CR+LF，对照版为 正文+LF。
        self.assertNotEqual(first_cr.stdout, first_crlf.stdout)
        self.assertEqual(
            first_cr.stdout, first_crlf.stdout[:-1] + b"\r\n"
        )
        self.assertEqual(first_cr.stderr, first_crlf.stderr)
        self.assertEqual(first_cr.returncode, first_crlf.returncode)
        # 第二次显示、警告与退出码逐字节/逐值相同。
        self.assertEqual(second_cr.stdout, second_crlf.stdout)
        self.assertEqual(second_cr.stderr, second_crlf.stderr)
        self.assertEqual(second_cr.stderr, b"")
        self.assertEqual(second_cr.returncode, second_crlf.returncode)
        self.assertEqual(second_cr.returncode, 0)
        # 且第二次显示并不等于任何一次导出：它有行号前缀与平台换行。
        self.assertNotEqual(second_cr.stdout, first_cr.stdout)
        self.assertNotEqual(second_cr.stdout, first_crlf.stdout)

    def test_reimport_is_not_restore_of_original_file(self):
        """再次读取得到的是新文件视图，不是原文件：行号、正文 CR、警告都不同。"""
        source = self._write(BARE_CR_INPUT, "restore-source.jsonl")
        first = run_cli_bytes(source, "--level", "ERROR", "--jsonl")
        self.assertEqual(first.returncode, 0, first.stderr)
        exported = self._write(first.stdout, "restore-exported.jsonl")
        second = run_cli_bytes(exported, "--level", "ERROR")
        # 直接对原文件跑默认模式：行号是 4，末行单独 CR 留在正文里，
        # 标准错误仍带第 3 行警告。
        direct = run_cli_bytes(source, "--level", "ERROR")
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(
            direct.stdout,
            b"4\t" + LINE4 + b"\r" + PLATFORM_NEWLINE,
        )
        self.assertEqual(direct.stderr, FIRST_STAGE_STDERR)
        # 再次读取：行号从 1 起算、正文末 CR 已消失、标准错误为空——
        # 与直接读取原文件逐字节不同，不能解释为恢复原文件。
        self.assertEqual(second.stdout, SECOND_STAGE_STDOUT)
        self.assertNotEqual(second.stdout, direct.stdout)
        self.assertEqual(second.stderr, b"")
        self.assertNotEqual(second.stderr, direct.stderr)


if __name__ == "__main__":
    unittest.main()
