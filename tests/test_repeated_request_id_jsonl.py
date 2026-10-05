"""重复 --request-id 与 --jsonl 导出组合的离线回归测试。

验证命令行的既有行为：重复提供 --request-id 时多个标识取并集，再与级别
条件取交集；启用 --jsonl 后标准输出只写匹配记录的原始正文各补一个 LF，
重复候选不会造成重复导出。产品代码、公开接口与文档行为均不改动，
本文件只新增可由 ``python -m unittest discover -s tests`` 发现的测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / os），
测试输入均在用例自建的临时目录中以 UTF-8 生成并在用例结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化；导出模式直接写字节流，记录终止符固定为 LF，
标准错误沿用 print 的平台终止换行（os.linesep，Linux 上为 LF）。

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

# print（标准错误警告）在当前平台的记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# ---------------------------------------------------------------------------
# 核心样例的八个物理行（不含行分隔符）：
# 第 1 行 r-2 / ERROR；第 2 行空行；第 3 行非法 JSON；
# 第 4 行 r-1，级别为首尾各带一个空格的小写 error（规范化后为 ERROR）；
# 第 5 行 r-1 / INFO（级别不匹配）；第 6 行 R-1（大小写不同）；
# 第 7 行标识首尾各带一个空格（值不被裁剪）；第 8 行 r-12（仅为子串）。
# ---------------------------------------------------------------------------
CORE_LINES = [
    '{"level":"ERROR","request_id":"r-2"}',
    "",
    "not-json",
    '{"level":" error ","request_id":"r-1"}',
    '{"level":"INFO","request_id":"r-1"}',
    '{"level":"ERROR","request_id":"R-1"}',
    '{"level":"ERROR","request_id":" r-1 "}',
    '{"level":"ERROR","request_id":"r-12"}',
]
# 选择 ERROR、同时指定 r-1 与 r-2 并启用 --jsonl 时：标识并集 {r-1, r-2}
# 与 ERROR 级别取交集，只导出第 1、4 行原始正文，各补一个 LF；
# 没有行号、制表符前缀、数组或摘要，第 4 行 level 值中的空格和小写原样保留。
CORE_EXPORT_BYTES = (CORE_LINES[0] + "\n" + CORE_LINES[3] + "\n").encode("utf-8")
# 标准错误：仅第 3 行一条 JSON 解析失败警告（print 终止换行随平台）。
CORE_WARNING_BYTES = (
    "第 3 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)


def run_cli_bytes(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖导出字节与输入行分隔符的差异。
    """
    env = dict(os.environ)
    # 固定子进程的输出编码与 argparse 等标准库消息语言，使断言跨环境稳定；
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


class RepeatedRequestIdJsonlExportTests(unittest.TestCase):
    """--jsonl 路径上的标识并集：导出字节、警告与退出码与候选写法无关。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "core.jsonl")
        Path(self.path).write_bytes(
            ("\n".join(CORE_LINES) + "\n").encode("utf-8")
        )

    def _assert_core_export(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出逐字节等于第 1 行原文 + LF，再接第 4 行原文 + LF。
        self.assertEqual(proc.stdout, CORE_EXPORT_BYTES)
        # 没有行号、制表符前缀、数组或摘要字段。
        self.assertFalse(proc.stdout.startswith(b"1\t"))
        self.assertNotIn(b"\t", proc.stdout)
        self.assertNotIn(b"[", proc.stdout)
        self.assertNotIn(b"matched_count", proc.stdout)
        # 第 4 行 level 值中的空格和小写原样保留（正文不重新序列化）。
        self.assertIn('"level":" error "'.encode("utf-8"), proc.stdout)
        # 每条记录恰以一个 LF 结束（共两个），无 CRLF、无多余结尾。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r", proc.stdout)
        # 大小写不同、标识带空格、仅为子串以及级别不同的记录都不进入导出。
        self.assertNotIn(b'"R-1"', proc.stdout)
        self.assertNotIn(b'" r-1 "', proc.stdout)
        self.assertNotIn(b"r-12", proc.stdout)
        self.assertNotIn(b"INFO", proc.stdout)
        # 标准错误只有第 3 行的 JSON 解析失败警告及末尾换行。
        self.assertEqual(proc.stderr, CORE_WARNING_BYTES)

    def test_two_request_ids_export_union_intersected_with_level(self):
        proc = run_cli_bytes(
            self.path, "--level", "ERROR",
            "--request-id", "r-1", "--request-id", "r-2",
            "--jsonl",
        )
        self._assert_core_export(proc)

    def test_request_id_order_does_not_change_export(self):
        proc = run_cli_bytes(
            self.path, "--level", "ERROR",
            "--request-id", "r-2", "--request-id", "r-1",
            "--jsonl",
        )
        self._assert_core_export(proc)

    def test_duplicate_request_ids_do_not_duplicate_export(self):
        repetitions = (
            ("--request-id", "r-1", "--request-id", "r-1",
             "--request-id", "r-2"),
            ("--request-id", "r-2", "--request-id", "r-2",
             "--request-id", "r-1"),
            ("--request-id", "r-1", "--request-id", "r-2",
             "--request-id", "r-1", "--request-id", "r-2"),
        )
        for extra in repetitions:
            with self.subTest(extra):
                proc = run_cli_bytes(
                    self.path, "--level", "ERROR", *extra, "--jsonl"
                )
                self._assert_core_export(proc)

    def test_lf_and_crlf_inputs_export_identical_bytes(self):
        payloads = {
            "LF": ("\n".join(CORE_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(CORE_LINES) + "\r\n").encode("utf-8"),
        }
        for label, payload in payloads.items():
            with self.subTest(label):
                path = Path(self._tmp.name) / f"core-{label}.jsonl"
                path.write_bytes(payload)
                proc = run_cli_bytes(
                    str(path), "--level", "ERROR",
                    "--request-id", "r-1", "--request-id", "r-2",
                    "--jsonl",
                )
                self._assert_core_export(proc)

    def test_unmatched_request_id_exports_nothing_but_keeps_warning(self):
        # 仅选择不存在的 r-9：标准输出为空，第 3 行警告保留，退出码仍为 0。
        proc = run_cli_bytes(
            self.path, "--level", "ERROR", "--request-id", "r-9", "--jsonl"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, CORE_WARNING_BYTES)


class RepeatedRequestIdJsonlArgumentErrorTests(unittest.TestCase):
    """--jsonl 路径上的参数边界：任一空标识即整体拒绝，先于读文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 使用确定不存在的路径：参数校验必须先于文件读取。
        self.missing = str(Path(self._tmp.name) / "no-such-file.jsonl")

    def test_empty_or_blank_value_rejected_before_reading_file(self):
        bad_values = ("", "   ", "\t")
        for bad in bad_values:
            for position, request_ids in (
                ("空值在合法值之前", (bad, "r-1")),
                ("空值在合法值之后", ("r-1", bad)),
                ("合法值夹住空值", ("r-2", bad, "r-1")),
            ):
                with self.subTest(value=repr(bad), position=position):
                    args = [self.missing, "--level", "ERROR", "--jsonl"]
                    for request_id in request_ids:
                        args += ["--request-id", request_id]
                    proc = run_cli_bytes(*args)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, b"")
                    stderr = proc.stderr.decode("utf-8")
                    # 标准错误指出该选项的参数错误。
                    self.assertIn("参数错误", stderr)
                    self.assertIn("--request-id", stderr)
                    # 参数校验在读取文件之前：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", stderr)


if __name__ == "__main__":
    unittest.main()
