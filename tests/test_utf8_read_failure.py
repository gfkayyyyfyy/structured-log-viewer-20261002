"""文件尾部 UTF-8 编码损坏的读取失败回归测试。

现有测试只覆盖普通逐行模式下文件中出现非法字节的情形；本模块补充
“文件尾部编码损坏”这一读取边界，并在三种输出模式（默认逐行、--summary、
--jsonl）下统一验证：整份文件 UTF-8 解码失败时必须整体判定为文件读取
失败，不能因为前面已经存在可匹配记录或损坏 JSON 行而留下任何部分结果，
也不能泄漏逐行解析阶段才会产生的“无效日志”警告。

测试输入全部按字节在用例自建的临时目录中构造并自动清理：
- 完整前缀依次为三个物理行，每行以 LF 结束：
  1. ``{"level":"ERROR","message":"失败"}``（--level ERROR 下本应匹配）
  2. ``not-json``（解码成功时本应产生一条 JSON 解析失败警告）
  3. ``{"level":"INFO","message":"正常"}``（级别不匹配）
- 两份损坏文件分别在前缀末尾（不再追加换行）拼接：
  - 单个非法字节 ``0xFF``；
  - 未完成的 UTF-8 多字节序列 ``0xE4 0xB8``（三字节序列缺少尾字节）。
- 另以前缀本身（三行均以 LF 结束，不追加任何损坏字节）作为成功对照。

每份损坏文件分别以默认逐行输出、--summary、--jsonl 运行
``python -m log_viewer <路径> --level ERROR``（两个开关分别单独使用），
所有组合均要求：退出码 2、标准输出严格为空、标准错误包含“文件读取失败”
与输入路径，且不包含“无效日志”或 Traceback；不断言底层异常的完整文案、
字节偏移或操作系统附加细节。

成功对照在相同级别下要求：退出码 0；标准错误只有
“第 2 行：无效日志：JSON 解析失败”及末尾换行；默认模式标准输出只有
第 1 行的行号、制表符与原文；--jsonl 标准输出只有第 1 行原文加一个 LF；
--summary 标准输出中 matched_count 与 invalid_count 均为 1，
by_level 的 ERROR 为 1、其余四个级别均为 0。

比较按原始字节进行（不开 text=True、不做通用换行归一化），
记录自身的终止换行沿用运行平台现有行为（os.linesep）；
退出码、标准输出、标准错误分别独立断言，失败时可区分错误分类、
部分输出与警告泄漏。

只用 Python 标准库，不依赖 sample.jsonl 等外部样例、网络或外部服务；
产品源码、公开命令参数、模块返回约定与现有文档均不改动。

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

# print 写标准输出/标准错误时的平台记录终止换行，按字节比较；
# 子进程文本层的换行翻译随当前平台，不做归一化。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 三个物理行的正文（不含行分隔符）。
LINE1 = '{"level":"ERROR","message":"失败"}'
LINE2 = "not-json"
LINE3 = '{"level":"INFO","message":"正常"}'

# 完整前缀：三行依次以 LF 结束。
PREFIX_BYTES = (
    LINE1.encode("utf-8") + b"\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3.encode("utf-8") + b"\n"
)

# 两种尾部编码损坏：均直接拼在前缀末尾，末尾不追加换行。
CORRUPT_PAYLOADS = {
    "尾部单个非法字节 0xFF": PREFIX_BYTES + b"\xff",
    "尾部未完成 UTF-8 序列 0xE4 0xB8": PREFIX_BYTES + b"\xe4\xb8",
}

# 三种输出模式：两个开关分别单独使用，不组合。
MODES = (
    ("默认逐行输出", ()),
    ("--summary 摘要", ("--summary",)),
    ("--jsonl 导出", ("--jsonl",)),
)

READ_FAILURE_MESSAGE = "文件读取失败".encode("utf-8")
INVALID_LOG_WARNING = "无效日志".encode("utf-8")
TRACEBACK = b"Traceback"

# 成功对照的标准错误：只有第 2 行的 JSON 解析失败警告（含末尾换行）。
SUCCESS_STDERR = (
    "第 2 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)

# 成功对照 --summary 的期望摘要：第 1 行匹配、第 2 行无效、第 3 行不匹配。
SUCCESS_SUMMARY = {
    "matched_count": 1,
    "invalid_count": 1,
    "by_level": {
        "DEBUG": 0,
        "INFO": 0,
        "WARNING": 0,
        "ERROR": 1,
        "CRITICAL": 0,
    },
}


def run_cli_bytes(path, *extra):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。

    不使用 text=True：避免通用换行翻译与严格解码在比较前改动子进程输出。
    """
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定；
    # 应用自身的中文消息不受这些变量影响。
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", str(path), "--level", "ERROR",
         *extra],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class FixtureSelfCheckTests(unittest.TestCase):
    """测试夹具自检：前缀与损坏后缀的字节形状必须符合用例意图。"""

    def test_prefix_is_three_lf_terminated_physical_lines(self):
        # 前缀严格解码为合法 UTF-8，且恰好包含三个 LF 行终止符。
        text = PREFIX_BYTES.decode("utf-8")
        self.assertEqual(PREFIX_BYTES.count(b"\n"), 3)
        self.assertTrue(PREFIX_BYTES.endswith(b"\n"))
        self.assertEqual(
            text.split("\n"),
            [LINE1, LINE2, LINE3, ""],
        )

    def test_corrupt_suffixes_are_undecodable_and_have_no_trailing_lf(self):
        for label, payload in CORRUPT_PAYLOADS.items():
            with self.subTest(label):
                # 损坏文件与前缀仅差尾部后缀，且末尾没有换行。
                self.assertTrue(payload.startswith(PREFIX_BYTES))
                self.assertFalse(payload.endswith(b"\n"))
                # 严格 UTF-8 解码必须失败，损坏位于文件尾部。
                with self.assertRaises(UnicodeDecodeError):
                    payload.decode("utf-8")
                # 前缀本身必须仍然完整合法，否则失败对照不成立。
                PREFIX_BYTES.decode("utf-8")


class TailCorruptionReadFailureTests(unittest.TestCase):
    """文件尾部编码损坏：三种输出模式下都整体读取失败、无部分结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, label, payload):
        path = Path(self._tmp.name) / f"{label}.jsonl"
        path.write_bytes(payload)
        return path

    def test_corrupted_tail_fails_in_every_output_mode(self):
        for corruption_label, payload in CORRUPT_PAYLOADS.items():
            path = self._write(
                # 文件名只使用 ASCII，避免路径自身引入编码变量。
                "ff" if payload.endswith(b"\xff") else "truncated",
                payload,
            )
            for mode_label, flags in MODES:
                with self.subTest(损坏=corruption_label, 模式=mode_label):
                    proc = run_cli_bytes(path, *flags)

                    # 1) 错误分类：整份文件解码失败按文件读取失败处理，
                    #    与参数错误、成功（0）区分开。
                    self.assertEqual(
                        proc.returncode, 2,
                        f"应退出 2，实际 {proc.returncode}；"
                        f"stderr={proc.stderr!r}",
                    )

                    # 2) 无部分输出：即使第 1 行本应匹配，标准输出必须
                    #    严格为空（逐行内容、JSONL 记录、JSON 摘要都不许有）。
                    self.assertEqual(
                        proc.stdout, b"",
                        f"读取失败时标准输出必须为空，实际 {proc.stdout!r}",
                    )

                    # 3) 警告泄漏检查：标准错误只报告文件读取失败，
                    #    不得进入逐行解析而输出“无效日志”或 Traceback；
                    #    错误中必须带上输入路径以便定位文件。
                    self.assertIn(READ_FAILURE_MESSAGE, proc.stderr)
                    self.assertIn(os.fsencode(str(path)), proc.stderr)
                    self.assertNotIn(
                        INVALID_LOG_WARNING, proc.stderr,
                        f"整份文件解码失败时不得输出逐行警告："
                        f"{proc.stderr!r}",
                    )
                    self.assertNotIn(
                        TRACEBACK, proc.stderr,
                        f"读取失败必须被捕获处理，不应出现 Traceback："
                        f"{proc.stderr!r}",
                    )
                    # 不断言底层异常完整文案、字节偏移或系统附加细节。

    def test_path_appears_in_failure_message_for_both_corruptions(self):
        # 单独固定“错误信息含输入路径”这一约定，两种损坏各自验证。
        for payload in CORRUPT_PAYLOADS.values():
            path = self._write(
                "ff" if payload.endswith(b"\xff") else "truncated",
                payload,
            )
            proc = run_cli_bytes(path)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn(
                "文件读取失败".encode("utf-8"),
                proc.stderr,
            )
            self.assertIn(os.fsencode(str(path)), proc.stderr)


class CleanPrefixSuccessControlTests(unittest.TestCase):
    """无损坏字节的同一前缀：三种模式行为正常，证明失败断言确由尾部损坏导致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "clean.jsonl"
        self.path.write_bytes(PREFIX_BYTES)

    def test_default_line_mode_outputs_only_first_line(self):
        proc = run_cli_bytes(self.path)
        # 退出码与两个输出流分别断言。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只有第 1 行：原始行号、制表符、原文，加平台记录终止换行。
        self.assertEqual(
            proc.stdout,
            b"1\t" + LINE1.encode("utf-8") + RECORD_TERMINATOR,
        )
        # 第 3 行 INFO 不匹配、不产生警告；标准错误只有第 2 行的警告。
        self.assertEqual(proc.stderr, SUCCESS_STDERR)

    def test_jsonl_mode_outputs_only_first_line_raw_with_single_lf(self):
        proc = run_cli_bytes(self.path, "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只有第 1 行原文及一个 LF：无行号制表符前缀，无外层数组或统计字段。
        self.assertEqual(proc.stdout, LINE1.encode("utf-8") + b"\n")
        self.assertEqual(proc.stderr, SUCCESS_STDERR)

    def test_summary_mode_counts_one_match_and_one_invalid(self):
        proc = run_cli_bytes(self.path, "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出是一个 JSON 对象加平台记录终止换行；解析后逐字段断言。
        self.assertTrue(
            proc.stdout.endswith(RECORD_TERMINATOR),
            f"摘要应以记录终止换行结束：{proc.stdout!r}",
        )
        summary = json.loads(
            proc.stdout[: -len(RECORD_TERMINATOR)].decode("utf-8")
        )
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["invalid_count"], 1)
        by_level = summary["by_level"]
        # ERROR 为 1，其余四个级别显式为 0，且不引入额外级别键。
        self.assertEqual(by_level, SUCCESS_SUMMARY["by_level"])
        self.assertEqual(
            set(by_level),
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        )
        self.assertEqual(summary, SUCCESS_SUMMARY)
        self.assertEqual(proc.stderr, SUCCESS_STDERR)


if __name__ == "__main__":
    unittest.main()
