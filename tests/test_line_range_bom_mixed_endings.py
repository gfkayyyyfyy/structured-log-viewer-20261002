"""行号区间筛选与 BOM、混合换行、正文分隔字符组合的离线回归测试。

在既有物理行号区间筛选测试之上，补充同一份日志中行号筛选、开头一次
UTF-8 BOM、混合行结束符（CRLF 与 LF 混用）、末行无换行以及正文内真实
U+2028/U+2029 与单独 CR 的组合验证，并逐字节核对原文导出。

主样例为开头恰有一个 UTF-8 BOM 的五个物理行：
- 第 1 行：{"level":"ERROR","message":"outside"}，CRLF 终止；
- 第 2 行：只含空格和制表符，LF 终止；
- 第 3 行：整行前两个空格，JSON 为
  {"level":" error ","message":"甲<U+2028>乙<U+2029>丙"}，
  JSON 后依次为一个单独 CR 和一个制表符，CRLF 终止
  （段内分隔符为真实字符，JSON 后那个 CR 不紧邻 LF，均属正文）；
- 第 4 行：{"level":"INFO"}，LF 终止；
- 第 5 行：not-json，没有末尾换行。

以 --level ERROR --line-range 3:3 查询时：
- 默认逐行模式：标准输出只有“原行号 3、制表符、第三行完整正文”和平台
  记录终止换行；真实 U+2028/U+2029 与单独 CR 不增加物理行号（否则
  命中行号与第 5 行警告行号都会错位），全文件诊断仍只有唯一警告
  “第 5 行：无效日志：JSON 解析失败”，退出码为 0；
- --summary：标准输出只含 matched_count、invalid_count、by_level
  三个既有字段并以换行结束，matched_count 与 invalid_count 均为 1，
  by_level 中 ERROR 为 1、其余四级为 0；警告与退出码同默认模式；
- --jsonl：标准输出逐字节等于第三行正文加一个 LF，保留前导空白、字段
  顺序、中文、真实分隔字符与单独 CR，不含 BOM 或行号前缀；警告与
  退出码同默认模式。

区间改为 1:1 时第 1 行以原行号 1 命中、正文不含 BOM，警告仍在第 5 行。
仅把已有行结束符统一换成 LF（末行仍无换行）后，3:3 查询的正文、
警告行号与摘要数值都不变。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
样例在临时目录中按字节构造并自动清理，不依赖预先存在的日志文件或网络；
不导入也不调用产品内部函数生成预期值，所有期望值在本模块独立字面给定；
产品源码与公开接口保持不变。通过 ``python -m log_viewer`` 入口核对
标准输出、标准错误（按原始字节捕获，不做通用换行归一化）与退出码。

为避免源码里出现不可见的行/段分隔字符，真实 U+2028/U+2029 一律以
chr(0x2028)/chr(0x2029) 在运行时构造（与 test_jsonl_export.py 一致）。

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# print（行号模式、标准错误警告、摘要）在当前平台的记录终止换行，按字节
# 比较；--jsonl 导出直接写字节流，终止符恒为单个 LF，不用本常量。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

BOM = b"\xef\xbb\xbf"

# 真实 U+2028 LINE SEPARATOR 与 U+2029 PARAGRAPH SEPARATOR：运行时产生
# 真实字符，写盘即 UTF-8 字节，不是 JSON 文本里的反斜杠转义。
LS = chr(0x2028)
PS = chr(0x2029)

# ---------------------------------------------------------------------------
# 五个物理行的正文（不含行结束符）。
# ---------------------------------------------------------------------------
LINE1 = '{"level":"ERROR","message":"outside"}'
# 第 2 行：只含空格和制表符。
LINE2 = " \t "
# 第 3 行：前导两个空格；level 规范化前为带首尾空白的小写 " error "；
# message 中“甲/乙”之间是真实 U+2028，“乙/丙”之间是真实 U+2029；
# JSON 对象之后依次是一个单独 CR 和一个制表符（均属正文）。
LINE3_MESSAGE = "甲" + LS + "乙" + PS + "丙"
LINE3_JSON = '{"level":" error ","message":"' + LINE3_MESSAGE + '"}'
LINE3_BODY = "  " + LINE3_JSON + "\r\t"
LINE4 = '{"level":"INFO"}'
LINE5 = "not-json"

# 主样例字节：开头一次 BOM；第 1、3 行 CRLF 终止，第 2、4 行 LF 终止，
# 第 5 行无末尾换行。
MIXED_BYTES = (
    BOM
    + LINE1.encode("utf-8") + b"\r\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3_BODY.encode("utf-8") + b"\r\n"
    + LINE4.encode("utf-8") + b"\n"
    + LINE5.encode("utf-8")
)

# 仅把已有行结束符统一换成 LF 的变体：正文一个字节都不动
# （第 3 行正文内的单独 CR 与制表符保留），末行仍无换行。
LF_ONLY_BYTES = (
    BOM
    + LINE1.encode("utf-8") + b"\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3_BODY.encode("utf-8") + b"\n"
    + LINE4.encode("utf-8") + b"\n"
    + LINE5.encode("utf-8")
)

# 全文件诊断唯一的警告（第 5 行），含 print 的平台终止换行。
WARNING_BYTES = (
    "第 5 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)

# 3:3 查询三种模式各自的标准输出期望值（独立字面给定，不调用产品函数）。
EXPECTED_DEFAULT_3 = b"3\t" + LINE3_BODY.encode("utf-8") + RECORD_TERMINATOR
EXPECTED_JSONL_3 = LINE3_BODY.encode("utf-8") + b"\n"
EXPECTED_DEFAULT_1 = b"1\t" + LINE1.encode("utf-8") + RECORD_TERMINATOR

# 真实分隔字符的 UTF-8 字节与字面 JSON 转义文本，用于区分“真实字符”
# 与“反斜杠+u2028/u2029”。
LS_UTF8 = LS.encode("utf-8")
PS_UTF8 = PS.encode("utf-8")
ESCAPED_2028 = b"\\u2028"
ESCAPED_2029 = b"\\u2029"

EXPECTED_BY_LEVEL = {
    "DEBUG": 0,
    "INFO": 0,
    "WARNING": 0,
    "ERROR": 1,
    "CRITICAL": 0,
}


def run_cli(path, *extra):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。

    不使用 text=True：文本模式会启用通用换行，把输出中的 CR、CRLF 都
    翻译成 LF，掩盖正文里单独 CR 的差异。
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


class LineRangeBomMixedEndingsTests(unittest.TestCase):
    """同一份 BOM + 混合换行 + 分隔字符日志上的行号区间组合验证。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.mixed_path = self.dir / "mixed.jsonl"
        self.mixed_path.write_bytes(MIXED_BYTES)
        self.lf_path = self.dir / "lf_only.jsonl"
        self.lf_path.write_bytes(LF_ONLY_BYTES)

    # -- 夹具自检 ----------------------------------------------------------

    def test_fixture_shapes(self):
        data = self.mixed_path.read_bytes()
        # 恰以一个 BOM 开头（不是两个连续标记）。
        self.assertTrue(data.startswith(BOM))
        self.assertFalse(data.startswith(BOM + BOM))
        # 四个行结束 LF = 五个物理行；末行没有末尾换行。
        self.assertEqual(data.count(b"\n"), 4)
        self.assertFalse(data.endswith(b"\n"))
        # 只有第 1、3 行用 CRLF 终止；第 3 行 JSON 后的 CR 紧邻制表符
        # 而非 LF，不产生额外 CRLF。
        self.assertEqual(data.count(b"\r\n"), 2)
        # 第 3 行段内确有“右花括号、单独 CR、制表符、CRLF”这一字节形状。
        self.assertIn(b'}\r\t\r\n', data)
        # 正文是真实 U+2028/U+2029 的 UTF-8 字节，而非字面转义文本。
        body_bytes = LINE3_BODY.encode("utf-8")
        self.assertIn(LS_UTF8, body_bytes)
        self.assertIn(PS_UTF8, body_bytes)
        self.assertNotIn(ESCAPED_2028, body_bytes)
        self.assertNotIn(ESCAPED_2029, body_bytes)
        # JSON 可解析，且 level 经规范化后为 ERROR、message 为真实字符串。
        record = json.loads(LINE3_JSON)
        self.assertEqual(record["level"], " error ")
        self.assertEqual(record["message"], LINE3_MESSAGE)
        # LF 变体：无 CRLF、四个 LF、末行无换行、正文内单独 CR 仍在。
        lf_data = self.lf_path.read_bytes()
        self.assertTrue(lf_data.startswith(BOM))
        self.assertEqual(lf_data.count(b"\r\n"), 0)
        self.assertEqual(lf_data.count(b"\n"), 4)
        self.assertFalse(lf_data.endswith(b"\n"))
        self.assertIn(b'}\r\t\n', lf_data)

    # -- 3:3 默认逐行模式 --------------------------------------------------

    def test_default_line_range_3_3(self):
        proc = run_cli(
            self.mixed_path, "--level", "ERROR", "--line-range", "3:3"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有行号 3、制表符和第三行完整正文，再接平台换行：
        # 前导两个空格、字段顺序、中文、真实分隔字符、JSON 后的单独 CR
        # 与制表符全部保留；命中行号为 3 本身就证明这些字符没有增加
        # 物理行号（第 1 行因区间外静默不输出）。
        self.assertEqual(proc.stdout, EXPECTED_DEFAULT_3)
        self.assertIn(LS_UTF8, proc.stdout)
        self.assertIn(PS_UTF8, proc.stdout)
        self.assertNotIn(ESCAPED_2028, proc.stdout)
        self.assertNotIn(ESCAPED_2029, proc.stdout)
        self.assertIn(b'}\r\t' + RECORD_TERMINATOR, proc.stdout)
        # 全文件诊断不受区间影响：唯一警告在第 5 行（空白行静默跳过）。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    # -- 3:3 摘要模式 ------------------------------------------------------

    def test_summary_line_range_3_3(self):
        proc = run_cli(
            self.mixed_path, "--level", "ERROR", "--line-range", "3:3",
            "--summary",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只是一个 JSON 对象加平台换行，不出现匹配正文或行号。
        self.assertTrue(proc.stdout.endswith(RECORD_TERMINATOR))
        summary = json.loads(
            proc.stdout[: -len(RECORD_TERMINATOR)].decode("utf-8")
        )
        # 只有既有三个字段。
        self.assertEqual(
            set(summary.keys()),
            {"matched_count", "invalid_count", "by_level"},
        )
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(summary["by_level"], EXPECTED_BY_LEVEL)
        self.assertNotIn(b"\t", proc.stdout)
        self.assertNotIn(BOM, proc.stdout)
        # 警告与退出码同默认模式。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    # -- 3:3 JSONL 原文导出 ------------------------------------------------

    def test_jsonl_line_range_3_3_byte_exact(self):
        proc = run_cli(
            self.mixed_path, "--level", "ERROR", "--line-range", "3:3",
            "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 逐字节等于第三行正文加一个 LF（与平台无关，不是 CRLF）。
        self.assertEqual(proc.stdout, EXPECTED_JSONL_3)
        self.assertEqual(proc.stdout.count(b"\n"), 1)
        self.assertNotIn(b"\r\n", proc.stdout)
        self.assertTrue(proc.stdout.endswith(b"\n"))
        # 正文形状逐字节保留：前导空白在前，单独 CR 与制表符在补充 LF 之前。
        self.assertTrue(proc.stdout.startswith(b"  {"))
        self.assertTrue(proc.stdout.endswith(b'}\r\t\n'))
        self.assertIn(
            '"level":" error ","message":'.encode("utf-8"), proc.stdout
        )
        # 真实分隔字符原样保留，未改写成 JSON 转义。
        self.assertIn(LS_UTF8, proc.stdout)
        self.assertIn(PS_UTF8, proc.stdout)
        self.assertNotIn(ESCAPED_2028, proc.stdout)
        self.assertNotIn(ESCAPED_2029, proc.stdout)
        # 不含 BOM，也不带行号与制表符前缀。
        self.assertNotIn(BOM, proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"3\t"))
        # 警告与退出码同默认模式。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    # -- 1:1：首行命中且正文无 BOM ----------------------------------------

    def test_line_range_1_1_keeps_original_number_one_without_bom(self):
        proc = run_cli(
            self.mixed_path, "--level", "ERROR", "--line-range", "1:1"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 第 1 行以原行号 1 命中，正文不含 BOM。
        self.assertEqual(proc.stdout, EXPECTED_DEFAULT_1)
        self.assertNotIn(BOM, proc.stdout)
        # 警告仍是全文件诊断：第 5 行。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    # -- 全部换成 LF：结果不变 ---------------------------------------------

    def test_lf_only_endings_keep_body_warning_and_summary(self):
        # 默认逐行：正文（含真实分隔字符与单独 CR）与警告行号不变。
        proc = run_cli(
            self.lf_path, "--level", "ERROR", "--line-range", "3:3"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPECTED_DEFAULT_3)
        self.assertEqual(proc.stderr, WARNING_BYTES)
        # 摘要数值不变。
        proc_summary = run_cli(
            self.lf_path, "--level", "ERROR", "--line-range", "3:3",
            "--summary",
        )
        self.assertEqual(proc_summary.returncode, 0, proc_summary.stderr)
        summary = json.loads(
            proc_summary.stdout[: -len(RECORD_TERMINATOR)].decode("utf-8")
        )
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(summary["by_level"], EXPECTED_BY_LEVEL)
        self.assertEqual(proc_summary.stderr, WARNING_BYTES)
        # 导出字节同样与混合换行版本完全一致。
        proc_jsonl = run_cli(
            self.lf_path, "--level", "ERROR", "--line-range", "3:3",
            "--jsonl",
        )
        self.assertEqual(proc_jsonl.returncode, 0, proc_jsonl.stderr)
        self.assertEqual(proc_jsonl.stdout, EXPECTED_JSONL_3)
        self.assertEqual(proc_jsonl.stderr, WARNING_BYTES)


if __name__ == "__main__":
    unittest.main()
