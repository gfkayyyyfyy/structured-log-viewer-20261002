"""--jsonl 导出开关的离线回归测试。

启用 --jsonl 后匹配记录以 JSONL 写到标准输出：每条记录只含原始正文与
末尾换行，没有行号和制表符前缀，也没有外层数组或统计字段，可直接
重定向保存后再次导入。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中生成，不依赖 sample.jsonl 或外部服务。

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

# 验收用的四个物理行：第 1 行级别不匹配，第 2 行空白，第 3 行非法 JSON，
# 第 4 行首尾各两个空格、规范化后为 ERROR；末行不带终止换行。
ACCEPTANCE_LINES = [
    '{"level":"INFO"}',
    "",
    "not-json",
    '  {"level":" error ","message":"失败"}  ',
]
EXPECTED_STDERR = "第 3 行：无效日志：JSON 解析失败\n"


def run_cli_bytes(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖导出正文里单独 CR 的差异。
    """
    env = dict(os.environ)
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
    """--jsonl：只导出原始正文加末尾 LF，警告与退出码规则不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, payload: bytes, name="case.jsonl") -> str:
        path = Path(self._tmp.name) / name
        path.write_bytes(payload)
        return str(path)

    def test_acceptance_four_lines_last_without_newline(self):
        path = self._write("\n".join(ACCEPTANCE_LINES).encode("utf-8"))
        proc = run_cli_bytes(path, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只含第 4 行正文与末尾 LF：两端空格保留，行号、制表符均不出现。
        self.assertEqual(
            proc.stdout,
            (ACCEPTANCE_LINES[3] + "\n").encode("utf-8"),
        )
        self.assertEqual(proc.stderr, EXPECTED_STDERR.encode("utf-8"))

    def test_lf_crlf_and_final_newline_export_identically(self):
        # 物理行分隔符按既有规则去除：LF 与 CRLF、末行有无终止换行，
        # 导出内容逐字节一致。
        payloads = {
            "LF，末行有换行": ("\n".join(ACCEPTANCE_LINES) + "\n").encode("utf-8"),
            "CRLF": ("\r\n".join(ACCEPTANCE_LINES) + "\r\n").encode("utf-8"),
            "末行无换行符": "\n".join(ACCEPTANCE_LINES).encode("utf-8"),
        }
        expected = (ACCEPTANCE_LINES[3] + "\n").encode("utf-8")
        for label, payload in payloads.items():
            with self.subTest(label):
                path = self._write(payload, name=f"case-{label}.jsonl")
                proc = run_cli_bytes(path, "--level", "ERROR", "--jsonl")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, expected)
                self.assertEqual(
                    proc.stderr, EXPECTED_STDERR.encode("utf-8")
                )

    def test_multiple_matches_keep_file_order_each_lf_terminated(self):
        lines = [
            '{"level":"ERROR","message":"第一条"}',
            "",
            '{"level":" error "}  ',
        ]
        path = self._write(("\n".join(lines) + "\n").encode("utf-8"))
        proc = run_cli_bytes(path, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            (lines[0] + "\n" + lines[2] + "\n").encode("utf-8"),
        )
        self.assertEqual(proc.stderr, b"")

    def test_body_not_reserialized_spaces_chinese_and_escapes_kept(self):
        # 首尾空白、中文以及 JSON 文本中的 \uXXXX 转义序列都必须原样保留，
        # 不得重新解码再序列化（转义形式不得变成真实字符，反之亦然）。
        line = '  {"level":"ERROR","message":"失败\\u2028"}  '
        path = self._write((line + "\n").encode("utf-8"))
        proc = run_cli_bytes(path, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, (line + "\n").encode("utf-8"))

    def test_lone_cr_and_real_u2028_u2029_kept_verbatim(self):
        # 正文中的真实 U+2028/U+2029 与不紧邻 LF 的单独 CR（含末行无 LF
        # 时结尾的 CR）均原样保留，且仍补一个末尾 LF。
        line1 = (
            '{"level":"ERROR","message":"甲'
            + " "
            + "乙"
            + " "
            + '丙"}'
        )
        line5 = '  {"level":" error ","message":"末条"}\r\t'
        payload = (
            line1 + "\r\n" + " \t " + "\n" + "not-json\n"
            + '{"level":"INFO"}\r\n' + line5
        ).encode("utf-8")
        path = self._write(payload)
        proc = run_cli_bytes(path, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            line1.encode("utf-8") + b"\n" + line5.encode("utf-8") + b"\n",
        )
        self.assertEqual(proc.stderr, EXPECTED_STDERR.encode("utf-8"))

    def test_empty_file_and_no_match_produce_empty_stdout(self):
        empty = self._write(b"", name="empty.jsonl")
        proc = run_cli_bytes(empty, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

        no_match = self._write(
            '{"level":"INFO"}\n'.encode("utf-8"), name="info.jsonl"
        )
        proc = run_cli_bytes(no_match, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def test_exported_file_reimports_with_default_output(self):
        # 导出结果重定向保存后，可经既有入口再次查看：第 1 行行号、
        # 制表符与相同正文，无警告，退出码 0。
        path = self._write("\n".join(ACCEPTANCE_LINES).encode("utf-8"))
        exported = Path(self._tmp.name) / "result.jsonl"
        proc = run_cli_bytes(path, "--level", "ERROR", "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        exported.write_bytes(proc.stdout)
        reimported = run_cli_bytes(
            str(exported), "--level", "ERROR"
        )
        self.assertEqual(reimported.returncode, 0, reimported.stderr)
        self.assertEqual(
            reimported.stdout,
            ("1\t" + ACCEPTANCE_LINES[3] + "\n").encode("utf-8"),
        )
        self.assertEqual(reimported.stderr, b"")


class JsonlFilterCompositionTests(unittest.TestCase):
    """--jsonl 与既有筛选条件组合：语义不变，输出形态为纯正文。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "case.jsonl")

    def _write(self, lines):
        Path(self.path).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_repeated_levels_dont_duplicate_exported_records(self):
        lines = [
            '{"level":"INFO"}',
            '{"level":"ERROR"}',
            '{"level":" error "}',
        ]
        self._write(lines)
        proc = run_cli_bytes(
            self.path,
            "--level", "ERROR", "--level", " error ", "--level", "INFO",
            "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            ("\n".join(lines) + "\n").encode("utf-8"),
        )

    def test_request_id_and_since_still_apply_and_warn_with_lineno(self):
        lines = [
            '{"level":"INFO","timestamp":null}',
            '{"level":"ERROR","request_id":"r-1","timestamp":"2026-10-03T10:00:00Z"}',
            '{"level":"ERROR","request_id":"r-1"}',
        ]
        self._write(lines)
        proc = run_cli_bytes(
            self.path, "--level", "ERROR", "--request-id", "r-1",
            "--since", "2026-10-03T10:00:00Z", "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, (lines[1] + "\n").encode("utf-8"))
        # 级别未命中但启用时间检查的行、缺 timestamp 的命中行都仍警告。
        self.assertEqual(
            proc.stderr.decode("utf-8"),
            "第 1 行：无效日志：timestamp 缺失或格式无效\n"
            "第 3 行：无效日志：timestamp 缺失或格式无效\n",
        )


class JsonlSummaryConflictTests(unittest.TestCase):
    """--jsonl 与 --summary 互斥：读取文件前拒绝，退出码 2。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "log.jsonl")
        Path(self.path).write_text(
            '{"level":"ERROR"}\n', encoding="utf-8"
        )

    def test_conflict_rejected_before_reading_file(self):
        missing = str(Path(self._tmp.name) / "no-such-file.jsonl")
        proc = run_cli_bytes(
            missing, "--level", "ERROR", "--jsonl", "--summary"
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("参数错误", stderr)
        self.assertIn("--jsonl", stderr)
        self.assertIn("--summary", stderr)
        # 未读取文件：不应出现读取失败信息或部分结果。
        self.assertNotIn("文件读取失败", stderr)

    def test_same_conflict_regardless_of_flag_order(self):
        proc = run_cli_bytes(
            self.path, "--level", "ERROR", "--summary", "--jsonl"
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("参数错误", proc.stderr.decode("utf-8"))

    def test_jsonl_is_a_flag_and_takes_no_value(self):
        proc = run_cli_bytes(
            self.path, "--level", "ERROR", "--jsonl", "x"
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")

    def test_without_jsonl_default_output_unchanged(self):
        proc = run_cli_bytes(self.path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b'1\t{"level":"ERROR"}\n')
        self.assertEqual(proc.stderr, b"")


if __name__ == "__main__":
    unittest.main()
