"""物理行号区间筛选与混合换行、正文分隔字符、BOM 组合的回归测试。

在既有 --line-range 用例之外补充：同一份日志中同时出现

- 文件开头恰有一次 UTF-8 BOM（字节 EF BB BF）；
- CRLF 与 LF 混用的物理行终止符，且末行没有终止换行；
- JSON 字符串值内的真实 U+2028 LINE SEPARATOR 与 U+2029
  PARAGRAPH SEPARATOR（真实字符，不是 JSON 文本里的反斜杠转义）；
- JSON 对象之后不紧邻 LF 的单独 CR（其后紧跟制表符）；
- 全空白行与损坏 JSON 行。

主样例是五个物理行：

1. ``{"level":"ERROR","message":"outside"}``，CRLF 终止；
2. 只含空格和制表符的空白行，LF 终止；
3. 两个前导空格，JSON 为
   ``{"level":" error ","message":"甲<U+2028>乙<U+2029>丙"}``，
   JSON 对象后依次是一个真实 CR 和一个制表符，CRLF 终止；
4. ``{"level":"INFO"}``，LF 终止；
5. ``not-json``，没有末尾换行。

通过 ``python -m log_viewer`` 子进程入口逐字节核对标准输出、标准错误
与退出码；期望值在本模块独立给出（独立构造的常量），不调用产品内部
函数生成答案。以 ``--level ERROR --line-range 3:3`` 查询时：

- 默认逐行模式标准输出只有 ``3<Tab>第三行完整正文`` 加平台换行，
  BOM 不出现；正文中的真实 U+2028/U+2029 与单独 CR 不增加物理行号，
  全文件诊断仍只有唯一警告“第 5 行：无效日志：JSON 解析失败”，
  退出码 0；
- --summary 标准输出只含 matched_count、invalid_count、by_level
  三个既有字段并以换行结束：matched_count 与 invalid_count 均为 1，
  by_level 中 ERROR 为 1，其余四级为 0；警告与退出码同默认模式；
- --jsonl 标准输出逐字节等于第三行正文加一个 LF：保留前导空白、
  末尾 CR 与制表符、字段顺序及真实分隔字符，不含 BOM 或行号前缀；
  警告与退出码同默认模式。

区间改为 1:1 时，第一行按原行号 1 命中且正文不含 BOM，警告仍在第 5 行。
把所有行结束符统一换成 LF（末行仍无换行）后，3:3 查询的正文、警告
行号与摘要数值都与混合换行版本一致。

比较输出时按原始字节捕获（不开 text=True），不做通用换行归一化；
默认逐行、摘要与标准错误的记录终止换行沿用运行平台行为
（os.linesep），--jsonl 导出固定补单个 LF。为避免源码里出现不可见的
行/段分隔字符，U+2028/U+2029 一律以 chr(码点) 在运行时构造。

只用 Python 3 标准库；样例在临时目录创建并自动清理，不依赖预先存在
的日志文件、网络或外部服务；产品源码与公开接口保持不变。

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

# print（默认逐行、摘要、标准错误）在当前平台的记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 文件开头恰有的一次 UTF-8 BOM。
BOM = b"\xef\xbb\xbf"

# JSON 字符串值内的真实 U+2028/U+2029：运行时产生的真实字符，
# 写盘即 UTF-8 字节 E2 80 A8 / E2 80 A9，不是反斜杠转义序列。
LS = chr(0x2028)
PS = chr(0x2029)

# 五个物理行的正文（不含行分隔符）。
LINE1 = '{"level":"ERROR","message":"outside"}'
# 第 2 行：只含空格和制表符，整行按空白行跳过。
LINE2 = " \t "
# 第 3 行：两个前导空格；level 原文带首尾空白且小写；
# message 中“甲”“乙”之间是真实 U+2028，“乙”“丙”之间是真实 U+2029；
# JSON 对象后依次是一个不紧邻 LF 的单独 CR 和一个制表符。
LINE3 = (
    '  {"level":" error ","message":"甲'
    + LS
    + "乙"
    + PS
    + '丙"}\r\t'
)
LINE4 = '{"level":"INFO"}'
LINE5 = "not-json"

# 主样例字节：开头一次 BOM；第 1、3 行 CRLF 终止，第 2、4 行 LF 终止，
# 第 5 行没有末尾换行。
MIXED_BYTES = (
    BOM
    + LINE1.encode("utf-8") + b"\r\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3.encode("utf-8") + b"\r\n"
    + LINE4.encode("utf-8") + b"\n"
    + LINE5.encode("utf-8")
)

# 对照样例：仅把已有行结束符统一换成 LF，其余字节（含 BOM、正文内 CR、
# 末行无换行）完全不变。
LF_ONLY_BYTES = (
    BOM
    + LINE1.encode("utf-8") + b"\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3.encode("utf-8") + b"\n"
    + LINE4.encode("utf-8") + b"\n"
    + LINE5.encode("utf-8")
)

# 全文件诊断的唯一警告：损坏 JSON 在第 5 物理行。
WARNING_LINE5 = (
    "第 5 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)

# --level ERROR --line-range 3:3 的各模式独立期望值。
RANGE_3_3_STDOUT = b"3\t" + LINE3.encode("utf-8") + RECORD_TERMINATOR
RANGE_3_3_JSONL = LINE3.encode("utf-8") + b"\n"
# 摘要文本独立书写：恰好三个既有字段，键顺序与产品默认序列化一致，
# 但不调用产品代码生成。
EXPECTED_SUMMARY = (
    '{"matched_count": 1, "invalid_count": 1, '
    '"by_level": {"DEBUG": 0, "INFO": 0, "WARNING": 0, '
    '"ERROR": 1, "CRITICAL": 0}}'
)
RANGE_3_3_SUMMARY = EXPECTED_SUMMARY.encode("utf-8") + RECORD_TERMINATOR

# --line-range 1:1 时第一行的期望输出（正文不含 BOM）。
RANGE_1_1_STDOUT = b"1\t" + LINE1.encode("utf-8") + RECORD_TERMINATOR
RANGE_1_1_JSONL = LINE1.encode("utf-8") + b"\n"


def run_cli_bytes(*args):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖正文中单独 CR 的差异。
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


class MixedNewlineBomFixtureTests(unittest.TestCase):
    """BOM + 混合换行 + 真实分隔字符主样例上的 3:3 / 1:1 组合结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "mixed.jsonl"
        self.path.write_bytes(MIXED_BYTES)

    def _run(self, *extra):
        return run_cli_bytes(
            str(self.path), "--level", "ERROR", *extra
        )

    def test_fixture_shape_matches_spec(self):
        # 夹具自检：开头恰有一次 BOM（全文件也仅此一次）。
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(BOM))
        self.assertFalse(data.startswith(BOM + BOM))
        self.assertEqual(data.count(BOM), 1)
        # 四个 LF 字节（两个 CRLF 中的 LF + 两个单独 LF）= 五个物理行；
        # 末行没有终止换行。
        self.assertEqual(data.count(b"\n"), 4)
        self.assertFalse(data.endswith(b"\n"))
        # 第 3 行以两个空格开始；对象后的单独 CR 与制表符之后才是 CRLF。
        self.assertIn(
            b'  {"level":" error ","message":"',
            data,
        )
        self.assertIn(b'}\r\t\r\n', data)
        self.assertFalse(LINE3.endswith(("\n", "\r\n")))
        # 第 3 行含真实 U+2028/U+2029 字符，而非 JSON 转义文本。
        self.assertIn(LS, LINE3)
        self.assertIn(PS, LINE3)
        self.assertNotIn("\\u2028", LINE3)
        self.assertNotIn("\\u2029", LINE3)
        self.assertIn(LS.encode("utf-8"), LINE3.encode("utf-8"))
        self.assertIn(PS.encode("utf-8"), LINE3.encode("utf-8"))
        # 第 2 行确实只含空格和制表符。
        self.assertTrue(LINE2)
        self.assertEqual(LINE2.strip(), "")
        self.assertIn("\t", LINE2)
        self.assertIn(" ", LINE2)

    def test_range_3_3_default_outputs_only_line_three_with_platform_newline(self):
        proc = self._run("--line-range", "3:3")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有原行号 3、制表符和第三行完整正文，再接平台换行。
        self.assertEqual(proc.stdout, RANGE_3_3_STDOUT)
        # 行号仍为 3 且警告仍在第 5 行：真实 U+2028/U+2029 与行内单独
        # CR 都没有被当成物理换行（否则行号会整体错位）。
        self.assertTrue(proc.stdout.startswith(b"3\t"))
        self.assertNotIn(b"1\t", proc.stdout)
        self.assertNotIn(b"5\t", proc.stdout)
        # BOM 不出现在输出正文中。
        self.assertNotIn(BOM, proc.stdout)
        # 全文件诊断唯一警告，带平台换行，退出码 0。
        self.assertEqual(proc.stderr, WARNING_LINE5)

    def test_range_3_3_summary_has_three_fields_expected_counts(self):
        proc = self._run("--line-range", "3:3", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出逐字节等于独立给定的摘要文本加平台换行：
        # 只有 matched_count、invalid_count、by_level 三个既有字段。
        self.assertEqual(proc.stdout, RANGE_3_3_SUMMARY)
        summary = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            set(summary), {"matched_count", "invalid_count", "by_level"}
        )
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 1,
             "CRITICAL": 0},
        )
        # 摘要中不泄漏命中正文。
        self.assertNotIn(LINE3.encode("utf-8"), proc.stdout)
        # 警告与退出码同默认模式。
        self.assertEqual(proc.stderr, WARNING_LINE5)

    def test_range_3_3_jsonl_is_raw_line_three_body_plus_single_lf(self):
        proc = self._run("--line-range", "3:3", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 逐字节等于第三行正文加一个 LF。
        self.assertEqual(proc.stdout, RANGE_3_3_JSONL)
        # 恰好一个 LF（导出补的终止符），且不存在 CRLF：正文里的单独
        # CR 后面紧跟制表符，必须原样保留。
        self.assertEqual(proc.stdout.count(b"\n"), 1)
        self.assertNotIn(b"\r\n", proc.stdout)
        self.assertTrue(proc.stdout.endswith(b"}\r\t\n"))
        # 不含 BOM，也不带行号与制表符前缀。
        self.assertNotIn(BOM, proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"3\t"))
        # 前导空白、字段顺序与真实分隔字符原样保留。
        self.assertTrue(proc.stdout.startswith(b"  {"))
        self.assertIn(
            b'"level":" error ","message":"', proc.stdout
        )
        self.assertIn(LS.encode("utf-8"), proc.stdout)
        self.assertIn(PS.encode("utf-8"), proc.stdout)
        # “甲<LS>乙<PS>丙”这一完整片段以真实字符字节出现。
        self.assertIn(
            ("甲" + LS + "乙" + PS + "丙").encode("utf-8"),
            proc.stdout,
        )
        # 警告与退出码同默认模式。
        self.assertEqual(proc.stderr, WARNING_LINE5)

    def test_range_1_1_matches_line_one_with_original_number_and_no_bom(self):
        proc = self._run("--line-range", "1:1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 第一行按原行号 1 命中，正文不含 BOM。
        self.assertEqual(proc.stdout, RANGE_1_1_STDOUT)
        self.assertTrue(proc.stdout.startswith(b"1\t"))
        self.assertNotIn(BOM, proc.stdout)
        # 警告仍在第五行，不受区间影响。
        self.assertEqual(proc.stderr, WARNING_LINE5)

    def test_range_1_1_jsonl_body_has_no_bom(self):
        proc = self._run("--line-range", "1:1", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 导出同样逐字节等于第一行正文加 LF，BOM 只在文件开头被排除一次。
        self.assertEqual(proc.stdout, RANGE_1_1_JSONL)
        self.assertNotIn(BOM, proc.stdout)
        self.assertEqual(proc.stderr, WARNING_LINE5)


class LfOnlyNewlineVariantTests(unittest.TestCase):
    """行结束符统一为 LF（末行仍无换行）后，3:3 结果与混合换行版一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "lf.jsonl"
        self.path.write_bytes(LF_ONLY_BYTES)

    def _run(self, *extra):
        return run_cli_bytes(
            str(self.path), "--level", "ERROR", *extra
        )

    def test_fixture_shape_is_lf_only_with_bare_final_line(self):
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(BOM))
        self.assertEqual(data.count(BOM), 1)
        self.assertEqual(data.count(b"\n"), 4)
        self.assertNotIn(b"\r\n", data)
        # 第 3 行正文内的单独 CR 仍在（其后是制表符而非 LF）。
        self.assertIn(b"}\r\t\n", data)
        self.assertFalse(data.endswith(b"\n"))

    def test_default_3_3_body_and_warning_unchanged(self):
        proc = self._run("--line-range", "3:3")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 正文（含前导空白、真实 U+2028/U+2029、末尾 CR 与制表符）不变。
        self.assertEqual(proc.stdout, RANGE_3_3_STDOUT)
        # 警告行号不变：仍在第 5 行。
        self.assertEqual(proc.stderr, WARNING_LINE5)

    def test_summary_3_3_counts_unchanged(self):
        proc = self._run("--line-range", "3:3", "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, RANGE_3_3_SUMMARY)
        summary = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 1,
             "CRITICAL": 0},
        )
        self.assertEqual(proc.stderr, WARNING_LINE5)

    def test_jsonl_3_3_bytes_unchanged(self):
        proc = self._run("--line-range", "3:3", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 导出逐字节相同：正文加一个 LF，正文内 CR 保留，无 CRLF。
        self.assertEqual(proc.stdout, RANGE_3_3_JSONL)
        self.assertNotIn(b"\r\n", proc.stdout)
        self.assertEqual(proc.stderr, WARNING_LINE5)


if __name__ == "__main__":
    unittest.main()
