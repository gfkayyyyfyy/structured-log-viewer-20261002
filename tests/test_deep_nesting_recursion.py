"""JSON 解码嵌套过深抛出 RecursionError 时的回归测试。

json.loads 解码嵌套层级超过解释器允许范围的 JSON 值时会抛出
RecursionError（例如 CPython 3.14 的 C 加速器在栈占用超限时抛出
“Stack overflow ... while decoding a JSON array”，更早版本则在
接近 sys.getrecursionlimit() 时抛出）。查看器把它与其他 JSON 解析
失败一视同仁：只跳过该物理行、按原始行号向标准错误输出一条
“无效日志：JSON 解析失败”警告、不输出异常堆栈，并继续处理后续行；
不新增深度阈值，也不调整解释器递归上限。

本模块只用标准库，在临时目录中生成输入文件并在用例结束时清理，
不依赖仓库样例或外部服务。过深记录本身是括号完整配对、只占一行
的合法结构 JSON，唯一的失败原因是嵌套过深；用例显式复核标准库
json.loads 在相同解释器设置下确实抛出 RecursionError，不以损坏
语法或模拟异常替代。

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

# 嵌套数组深度：在本机解释器（CPython 3.14，递归上限保持默认 1000，
# 栈溢出阈值约 5.8 万层）下远超 json.loads 的承受范围，必然抛出
# RecursionError；在更早版本（阈值接近递归上限）下同样必然抛出。
# 不修改 sys.getrecursionlimit()，也不设置任何递归相关环境变量。
DEEP_NESTING_DEPTH = 100_000

BEFORE_LINE = '{"level":"ERROR","message":"before"}'
# 顶层 level 为 ERROR，payload 是只占一行、括号完整配对的过深嵌套数组。
DEEP_LINE = (
    '{"level":"ERROR","payload":'
    + "[" * DEEP_NESTING_DEPTH
    + "]" * DEEP_NESTING_DEPTH
    + "}"
)
EMPTY_LINE = ""
INFO_LINE = '{"level":"INFO"}'
AFTER_LINE = '{"level":"ERROR","message":"after"}'

# 验收文件的五个物理行：有效 ERROR、过深嵌套 ERROR、空行、INFO、有效 ERROR。
FIVE_LINES = [BEFORE_LINE, DEEP_LINE, EMPTY_LINE, INFO_LINE, AFTER_LINE]

EXPECTED_STDOUT = (
    "1\t" + BEFORE_LINE + "\n"
    "5\t" + AFTER_LINE + "\n"
)
EXPECTED_STDERR = "第 2 行：无效日志：JSON 解析失败\n"

EXPECTED_BY_LEVEL = {
    "DEBUG": 0,
    "INFO": 0,
    "WARNING": 0,
    "ERROR": 2,
    "CRITICAL": 0,
}

# 对照组：可正常解码的较浅嵌套对象，仍为 ERROR 级别。
SHALLOW_LINE = '{"level":"ERROR","payload":{"a":[[1, 2], [3, {"deep": [true, null, "x"]}]]}}'


def run_cli(*args):
    """在不调整递归上限的默认环境下运行 ``python -m log_viewer``。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


class DeepNestingRecursionTests(unittest.TestCase):
    """过深嵌套记录按单行解析失败处理，不中断整个文件的处理。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _write(self, lines) -> str:
        path = Path(self._tmpdir.name) / (self.id().replace(".", "_") + ".jsonl")
        # LF 分隔物理行，末尾再补一个 LF；五条记录即五个物理行。
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        return str(path)

    def test_deep_line_makes_stdlib_json_loads_raise_recursion_error(self):
        # 前置复核：过深对象只占一行、括号完整配对，标准库 json.loads
        # 在当前解释器设置下确实抛出 RecursionError（既不是损坏语法导致
        # 的 JSONDecodeError，也不是用模拟异常替代）。
        self.assertNotIn("\n", DEEP_LINE)
        self.assertEqual(DEEP_LINE.count("["), DEEP_LINE.count("]"))
        with self.assertRaises(RecursionError):
            json.loads(DEEP_LINE)

    def test_deep_nested_line_is_skipped_and_processing_continues(self):
        path = self._write(FIVE_LINES)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        # 标准输出严格只有第 1、5 行：原行号、制表符和原始正文，顺序不变。
        self.assertEqual(proc.stdout, EXPECTED_STDOUT)
        # 标准错误严格只有第 2 行的一条警告，不含异常堆栈；
        # 空行（第 3 行）占行号但不产生警告。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        self.assertNotIn("Traceback", proc.stderr)

    def test_summary_counts_deep_line_as_single_invalid(self):
        path = self._write(FIVE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--summary")
        self.assertEqual(proc.returncode, 0)
        # 标准错误与逐行模式一致：只有第 2 行的一条警告。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        self.assertNotIn("Traceback", proc.stderr)
        # 标准输出只含一个 JSON 摘要及末尾换行。
        self.assertTrue(proc.stdout.endswith("\n"))
        self.assertEqual(proc.stdout.count("\n"), 1)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(summary["by_level"], EXPECTED_BY_LEVEL)
        self.assertEqual(
            set(summary),
            {"matched_count", "invalid_count", "by_level"},
        )

    def test_shallow_nested_record_still_matches(self):
        # 对照组：把第 2 行换成可正常解码的较浅嵌套对象（仍为 ERROR），
        # 第 1、2、5 行均按原文输出，不产生任何警告。
        lines = [BEFORE_LINE, SHALLOW_LINE, EMPTY_LINE, INFO_LINE, AFTER_LINE]
        path = self._write(lines)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            proc.stdout,
            "1\t" + BEFORE_LINE + "\n"
            "2\t" + SHALLOW_LINE + "\n"
            "5\t" + AFTER_LINE + "\n",
        )


if __name__ == "__main__":
    unittest.main()
