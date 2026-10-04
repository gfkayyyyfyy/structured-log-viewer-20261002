"""文件开头 UTF-8 BOM（EF BB BF）兼容的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / json / os），
测试输入全部按字节在用例自建的临时目录中构造并自动清理，不依赖
demo.jsonl、sample.jsonl、cr.jsonl、网络或外部服务；产品源码除文件入口
对开头一次标记的处理外不改动，iter_matches 等公开函数的调用方式与
返回约定保持原样。

规则（默认逐行、--jsonl、--summary 三种模式一致）：
- 仅当文件最前面三个字节恰为 EF BB BF 时，把这一次标记从日志正文排除，
  它不占物理行号；首条记录不因此成为损坏行；无标记文件结果保持不变。
- 标记之后按既有规则处理：正文首尾空白、字段顺序、中文、JSON 转义原样
  保留；LF、CRLF、末行无换行以及正文中的单独 CR 沿用现有拆行规则。
- 标记后仅有空白或整个文件只有标记时按空日志处理：逐行与导出输出为空，
  摘要 matched_count、invalid_count 与五个级别计数全部为零，退出码 0。
- 只移除最开头的一次标记：连续两个标记、空白之后或后续行 JSON 对象之前
  出现的标记属于正文，该行产生一条含原始行号的 JSON 解析失败警告并跳过，
  退出码 0。JSON 字符串值中的 U+FEFF 原样保留且照常参与筛选。
- 标记之后若存在非法 UTF-8 字节，仍按文件读取失败处理：退出码 2、
  标准输出为空、标准错误包含文件路径与“文件读取失败”，不输出部分结果
  或逐行警告。

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

# print 写标准输出/标准错误时的平台记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

BOM = b"\xef\xbb\xbf"

# 验收用四条物理行的正文（不含行分隔符）：ERROR 记录、空行、not-json、INFO。
LINE1 = '{"level":"ERROR","message":"失败"}'
LINE2 = "not-json"
LINE3 = '{"level":"INFO"}'

# 与 demo.jsonl 相同的字节形状：开头 BOM，四行之间三个 LF，末行无终止 LF。
ACCEPTANCE_BYTES = (
    BOM
    + LINE1.encode("utf-8") + b"\n"
    + b"\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3.encode("utf-8")
)

MODES = (
    ("默认逐行输出", ()),
    ("--summary 摘要", ("--summary",)),
    ("--jsonl 导出", ("--jsonl",)),
)

READ_FAILURE_MESSAGE = "文件读取失败".encode("utf-8")
INVALID_LOG_WARNING = "无效日志".encode("utf-8")

# 标准错误应只有第 3 行的 JSON 解析失败警告（含末尾换行）。
ACCEPTANCE_STDERR = (
    "第 3 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)


def run_cli_bytes(path, *extra, level="ERROR"):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", str(path), "--level", level,
         *extra],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class BomAcceptanceTests(unittest.TestCase):
    """开头带 BOM 的四行验收样例：三种模式逐字节核对。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "demo.jsonl"
        self.path.write_bytes(ACCEPTANCE_BYTES)

    def test_fixture_starts_with_bom_and_has_four_physical_lines(self):
        data = self.path.read_bytes()
        # 夹具自检：恰好以 EF BB BF 开头，含三个 LF（四条物理行），末行无 LF。
        self.assertTrue(data.startswith(BOM))
        self.assertEqual(data.count(b"\n"), 3)
        self.assertFalse(data.endswith(b"\n"))

    def test_default_line_mode_strips_marker_keeps_line_number_one(self):
        proc = run_cli_bytes(self.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有行号 1、一个制表符及无标记正文，加平台记录终止换行。
        self.assertEqual(
            proc.stdout,
            b"1\t" + LINE1.encode("utf-8") + RECORD_TERMINATOR,
        )
        # 标记不占物理行号：not-json 仍是第 3 行，且标准错误只有这一条警告。
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_jsonl_mode_outputs_raw_object_with_single_lf(self):
        proc = run_cli_bytes(self.path, "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只有该对象原文和一个 LF：无标记、无行号制表符、无外层包装。
        self.assertEqual(proc.stdout, LINE1.encode("utf-8") + b"\n")
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)

    def test_summary_mode_counts_one_match_one_invalid(self):
        proc = run_cli_bytes(self.path, "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.endswith(RECORD_TERMINATOR))
        summary = json.loads(
            proc.stdout[: -len(RECORD_TERMINATOR)].decode("utf-8")
        )
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 1,
             "CRITICAL": 0},
        )
        self.assertEqual(proc.stderr, ACCEPTANCE_STDERR)


class BomOnlyOrWhitespaceTests(unittest.TestCase):
    """标记后仅有空白或整个文件只有标记：按空日志处理，三种模式全零且退出 0。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, payload, flags):
        path = Path(self._tmp.name) / "empty.jsonl"
        path.write_bytes(payload)
        return run_cli_bytes(path, *flags)

    def test_empty_log_shapes(self):
        for label, payload in (
            ("只有标记", BOM),
            ("标记加单个 LF", BOM + b"\n"),
            ("标记加 CRLF", BOM + b"\r\n"),
            ("标记后仅空白行", BOM + b"  \t \n"),
            ("标记后多个空白行", BOM + b"\n \r\n\t\n"),
        ):
            for mode_label, flags in MODES:
                with self.subTest(形状=label, 模式=mode_label):
                    proc = self._run(payload, flags)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stderr, b"")
                    if flags == ("--summary",):
                        self.assertTrue(proc.stdout.endswith(RECORD_TERMINATOR))
                        summary = json.loads(
                            proc.stdout[: -len(RECORD_TERMINATOR)].decode("utf-8")
                        )
                        self.assertEqual(summary["matched_count"], 0)
                        self.assertEqual(summary["invalid_count"], 0)
                        self.assertEqual(
                            set(summary["by_level"]),
                            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
                        )
                        self.assertTrue(
                            all(v == 0 for v in summary["by_level"].values())
                        )
                    else:
                        # 逐行与导出输出严格为空。
                        self.assertEqual(proc.stdout, b"")


class OnlyLeadingMarkerRemovedTests(unittest.TestCase):
    """只移除最开头的一次标记：其余位置的 U+FEFF 均属正文。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, payload, *flags, level="ERROR"):
        path = Path(self._tmp.name) / "f.jsonl"
        path.write_bytes(payload)
        return run_cli_bytes(path, *flags, level=level)

    def test_two_consecutive_markers_warns_on_line_one(self):
        line = LINE1.encode("utf-8")
        proc = self._run(BOM + BOM + line + b"\n")
        self.assertEqual(proc.returncode, 0)
        # 第二个标记留在首行正文，json 无法解析：第 1 行警告，输出为空。
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：JSON 解析失败".encode("utf-8")
            + RECORD_TERMINATOR,
        )

    def test_marker_after_leading_whitespace_warns_on_line_one(self):
        line = LINE1.encode("utf-8")
        proc = self._run(b" " + BOM + line + b"\n")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：JSON 解析失败".encode("utf-8")
            + RECORD_TERMINATOR,
        )

    def test_marker_before_json_object_on_later_line_warns_with_its_number(self):
        # 第 1 行是合法 INFO（匹配）；第 2 行以标记开头，警告必须带行号 2。
        proc = self._run(
            LINE3.encode("utf-8") + b"\n" + BOM + LINE3.encode("utf-8") + b"\n",
            level="INFO",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout,
            b"1\t" + LINE3.encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(
            proc.stderr,
            "第 2 行：无效日志：JSON 解析失败".encode("utf-8")
            + RECORD_TERMINATOR,
        )

    def test_marker_inside_json_string_value_is_body_and_filters(self):
        # message 为 "a<U+FEFF>b"：标记是字符串值的一部分，记录合法可匹配。
        record = '{"level":"INFO","message":"a﻿b"}'
        payload = BOM + record.encode("utf-8") + b"\n"
        # 默认逐行与 --jsonl 都逐字节保留该标记。
        proc = self._run(payload, level="INFO")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, b"1\t" + record.encode("utf-8") + RECORD_TERMINATOR
        )
        proc = self._run(payload, "--jsonl", level="INFO")
        self.assertEqual(proc.stdout, record.encode("utf-8") + b"\n")
        # 包含该标记的子串条件命中；去掉标记的同一子串不命中。
        proc = self._run(
            payload, "--message-contains", "a﻿b", level="INFO"
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout, b"1\t" + record.encode("utf-8") + RECORD_TERMINATOR
        )
        proc = self._run(payload, "--message-contains", "ab", level="INFO")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")


class LineEndingAndBodyPreservationTests(unittest.TestCase):
    """开头标记不改变既有换行与正文保留规则。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, payload, *flags):
        path = Path(self._tmp.name) / "f.jsonl"
        path.write_bytes(payload)
        return run_cli_bytes(path, *flags)

    def test_crlf_after_bom_still_stripped_as_line_terminator(self):
        line = LINE1.encode("utf-8")
        proc = self._run(BOM + line + b"\r\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"1\t" + line + RECORD_TERMINATOR)

    def test_last_line_without_lf_is_still_output_complete(self):
        line = LINE1.encode("utf-8")
        # 末行无终止 LF：逐行模式由 print 补平台记录换行，--jsonl 补单个 LF。
        proc = self._run(BOM + line)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"1\t" + line + RECORD_TERMINATOR)
        proc = self._run(BOM + line, "--jsonl")
        self.assertEqual(proc.stdout, line + b"\n")

    def test_lone_cr_at_end_of_final_line_remains_in_body(self):
        line = b'{"level":"ERROR","message":"x"}'
        # 最后一个分段没有 LF：即使以 CR 结尾也原样保留（末行结尾的单独 CR）。
        proc = self._run(BOM + line + b"\r")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"1\t" + line + b"\r" + RECORD_TERMINATOR)
        proc = self._run(BOM + line + b"\r", "--jsonl")
        self.assertEqual(proc.stdout, line + b"\r\n")

    def test_plain_file_without_marker_is_unchanged(self):
        # 无标记的普通 UTF-8 文件：同字节内容与加标记版本相比，仅差开头标记，
        # 行号与正文完全一致。
        line = LINE1.encode("utf-8")
        proc_plain = self._run(line + b"\n")
        proc_bom = self._run(BOM + line + b"\n")
        self.assertEqual(proc_plain.returncode, 0)
        self.assertEqual(proc_plain.stdout, proc_bom.stdout)
        self.assertEqual(proc_plain.stderr, proc_bom.stderr)
        self.assertNotIn(b"\xef\xbb\xbf", proc_plain.stdout)


class InvalidUtf8AfterBomTests(unittest.TestCase):
    """标记之后存在非法 UTF-8 字节：三种模式都按文件读取失败处理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_corruption_after_marker_fails_in_every_mode(self):
        prefix = BOM + LINE1.encode("utf-8") + b"\nnot-json\n"
        for label, suffix in (
            ("尾部单个非法字节 0xFF", b"\xff"),
            ("未完成 UTF-8 序列", b"\xe4\xb8"),
        ):
            path = Path(self._tmp.name) / ("ff.jsonl" if suffix == b"\xff"
                                           else "truncated.jsonl")
            path.write_bytes(prefix + suffix)
            for mode_label, flags in MODES:
                with self.subTest(损坏=label, 模式=mode_label):
                    proc = run_cli_bytes(path, *flags)
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    # 不输出部分匹配结果或摘要。
                    self.assertEqual(proc.stdout, b"")
                    # 标准错误包含文件路径与读取失败提示，且不泄漏逐行警告。
                    self.assertIn(READ_FAILURE_MESSAGE, proc.stderr)
                    self.assertIn(os.fsencode(str(path)), proc.stderr)
                    self.assertNotIn(INVALID_LOG_WARNING, proc.stderr)


if __name__ == "__main__":
    unittest.main()
