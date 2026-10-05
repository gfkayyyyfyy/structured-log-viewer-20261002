"""重复 --request-id 与 --jsonl 组合使用的离线回归测试。

验证命令行已支持的既有行为：重复提供 --request-id 时多个标识取并集，
再与级别条件取交集；重复候选不会造成重复导出；--jsonl 导出只写匹配
记录的原始正文和末尾 LF，不带行号、制表符前缀、外层数组或统计字段。

只用 Python 3 标准库（unittest / subprocess / tempfile / os），
测试输入在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令
参数、默认逐行输出、摘要模式及其他筛选行为均保持现状。

核心输入共八个物理行：第 1 行 r-2 / ERROR；第 2 行为空行；第 3 行
not-json；第 4 行 r-1，级别为首尾各带一个空格的小写 error（规范化后
为 ERROR，导出时原文中的空格和小写原样保留）；第 5 行 r-1 / INFO
（级别不匹配）；第 6 行 R-1（大小写不同）；第 7 行标识首尾各带一个
空格（值不被裁剪）；第 8 行 r-12（仅为子串）。

执行 python -m log_viewer case.jsonl --level ERROR --request-id r-1
--request-id r-2 --jsonl 时：

- 退出码 0；标准输出逐字节等于第 1 行原文加 LF，再接第 4 行原文加
  LF；没有行号、制表符前缀、数组或摘要。
- 标准错误只有“第 3 行：无效日志：JSON 解析失败”及末尾换行。
- 大小写不同、标识带空格、仅为子串以及级别不同的记录都不进入导出，
  也不产生额外警告。
- 交换两个标识的出现顺序、额外重复指定 r-1、或把输入行分隔符在 LF
  与 CRLF 之间切换，导出字节、警告与退出码完全相同。
- 仅选择不存在的 r-9 时标准输出为空，第 3 行警告保留，退出码仍为 0。
- 任一次 --request-id 值为空字符串或全为空白时退出码为 2，标准输出
  为空，标准错误指出该选项的参数错误；即使另有合法候选且输入路径
  不存在，也不会出现文件读取失败。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化；标准错误的记录终止换行沿用 print 的平台换行
（os.linesep，Linux 上为 LF）。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# print 写到标准错误的记录终止换行，随平台，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# ---------------------------------------------------------------------------
# 八个物理行的正文（不含行分隔符）。
# ---------------------------------------------------------------------------
# 第 1 行：r-2 / ERROR。
LINE1 = '{"level":"ERROR","request_id":"r-2"}'
# 第 2 行：空行，静默跳过。
LINE2 = ""
# 第 3 行：损坏 JSON，产生唯一一条警告。
LINE3 = "not-json"
# 第 4 行：r-1，级别为首尾各带一个空格的小写 error（规范化后为 ERROR）。
LINE4 = '{"level":" error ","request_id":"r-1"}'
# 第 5 行：r-1 / INFO，级别不匹配。
LINE5 = '{"level":"INFO","request_id":"r-1"}'
# 第 6 行：R-1，大小写不同，不匹配 r-1。
LINE6 = '{"level":"ERROR","request_id":"R-1"}'
# 第 7 行：标识首尾各带一个空格，值不被裁剪，不匹配 r-1。
LINE7 = '{"level":"ERROR","request_id":" r-1 "}'
# 第 8 行：r-12，仅为子串，不匹配 r-1。
LINE8 = '{"level":"ERROR","request_id":"r-12"}'

CORE_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7, LINE8]

# 期望的标准输出：第 1 行原文 + LF，再接第 4 行原文 + LF，逐字节相等。
# 第 4 行 level 值中的空格和小写原样保留；没有行号、制表符前缀、
# 数组或摘要。
EXPORT_BYTES = (LINE1 + "\n" + LINE4 + "\n").encode("utf-8")

# 期望的标准错误：只有第 3 行的 JSON 解析失败警告及末尾换行。
WARNING_BYTES = (
    "第 3 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
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


class RepeatedRequestIdJsonlTests(unittest.TestCase):
    """重复 --request-id 与 --jsonl：并集与级别取交集后按原文导出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "core.jsonl"
        path.write_bytes(("\n".join(CORE_LINES) + "\n").encode("utf-8"))
        self.path = str(path)

    def _run(self, *extra_args, path=None):
        return run_cli_bytes(
            path or self.path, "--level", "ERROR", *extra_args, "--jsonl"
        )

    def _assert_core_result(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：八个物理行；第 4 行 level 值带首尾空格且为小写。
        self.assertEqual(len(CORE_LINES), 8)
        self.assertEqual(LINE2, "")
        self.assertIn('" error "', LINE4)
        self.assertNotIn('"ERROR"', LINE4)

    def test_union_intersected_with_level_exports_raw_lines(self):
        proc = self._run("--request-id", "r-1", "--request-id", "r-2")
        self._assert_core_result(proc)
        # 逐字节结构：第 1 行原文 + LF + 第 4 行原文 + LF，恰两个 LF。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r", proc.stdout)
        # 不带行号与制表符前缀，不是数组，也不含摘要字段。
        self.assertNotIn(b"\t", proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"["))
        self.assertNotIn(b"matched_count", proc.stdout)
        # 第 4 行 level 值中的空格和小写原样保留，未被规范化重写。
        self.assertIn('" error "'.encode("utf-8"), proc.stdout)
        # 大小写不同、带空格、子串及级别不符的标识都不进入导出。
        self.assertNotIn(b"R-1", proc.stdout)
        self.assertNotIn(" r-1 ".encode("utf-8"), proc.stdout)
        self.assertNotIn(b"r-12", proc.stdout)
        self.assertNotIn(b"INFO", proc.stdout)

    def test_option_order_and_duplicate_candidates_do_not_matter(self):
        # 交换两个标识的出现顺序，或额外重复指定 r-1：
        # 导出字节、警告与退出码完全相同，重复候选不会造成重复导出。
        variants = (
            ("--request-id", "r-2", "--request-id", "r-1"),
            ("--request-id", "r-1", "--request-id", "r-2",
             "--request-id", "r-1"),
            ("--request-id", "r-1", "--request-id", "r-1",
             "--request-id", "r-2"),
        )
        for extra in variants:
            with self.subTest(extra):
                proc = self._run(*extra)
                self._assert_core_result(proc)

    def test_lf_and_crlf_inputs_export_identical_bytes(self):
        payloads = {
            "LF": ("\n".join(CORE_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(CORE_LINES) + "\r\n").encode("utf-8"),
        }
        for index, (label, payload) in enumerate(payloads.items()):
            with self.subTest(label):
                path = Path(self._tmp.name) / f"core-{index}.jsonl"
                path.write_bytes(payload)
                proc = self._run(
                    "--request-id", "r-1", "--request-id", "r-2",
                    path=str(path),
                )
                self._assert_core_result(proc)

    def test_absent_request_id_exports_nothing_but_warning_kept(self):
        # 仅选择不存在的 r-9：标准输出为空，第 3 行警告保留，退出码 0。
        proc = self._run("--request-id", "r-9")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, WARNING_BYTES)


class RepeatedRequestIdJsonlArgumentErrorTests(unittest.TestCase):
    """--jsonl 路径上的 --request-id 参数边界：任一空值即整体拒绝。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用确定不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")

    def test_empty_or_blank_value_rejected_before_reading_file(self):
        # 任一次 --request-id 值为空字符串或全为空白：退出码 2，标准
        # 输出为空，标准错误指出该选项的参数错误；即使另有合法候选且
        # 输入路径不存在，也不出现文件读取失败。
        for bad in ("", "   ", "\t"):
            for position, request_ids in (
                ("空值在合法值之前", (bad, "r-1")),
                ("空值在合法值之后", ("r-1", bad)),
            ):
                with self.subTest(value=repr(bad), position=position):
                    args = [self.missing, "--level", "ERROR"]
                    for request_id in request_ids:
                        args += ["--request-id", request_id]
                    args.append("--jsonl")
                    proc = run_cli_bytes(*args)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, b"")
                    self.assertIn("参数错误".encode("utf-8"), proc.stderr)
                    self.assertIn(b"--request-id", proc.stderr)
                    self.assertNotIn("文件读取失败".encode("utf-8"), proc.stderr)


if __name__ == "__main__":
    unittest.main()
