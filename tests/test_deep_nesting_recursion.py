"""JSON 解码过深嵌套抛出 RecursionError 时按单行解析失败处理的回归测试。

标准库 json.loads 遇到嵌套远超解释器递归承载能力的单行 JSON 对象时会抛出
RecursionError。查看器的既有约定是把它等同于普通 JSON 解析失败：只跳过
该物理行、按原始物理行号向标准错误输出一条“无效日志：JSON 解析失败”警告、
不输出异常堆栈，前后的有效记录仍照常筛选，整个文件处理不中断，退出码为 0。

测试固定使用一个嵌套 100000 层数组的单行 JSON 对象：括号完整配对、只占
一个物理行，在常见 CPython 上无论 json 使用 C 加速扫描器（较新版本约在
5.8 万层触发其递归保护）还是纯 Python 扫描器（约在默认递归上限 1000 附近）
都会抛出 RecursionError。用例首先自检标准库 json.loads 在相同解释器设置下
（全程不调用 sys.setrecursionlimit、不新增深度阈值）确实对它抛出
RecursionError，而不是以损坏语法或模拟异常替代。

只用 Python 标准库；输入文件在用例自建的临时目录中以字节写盘（保证物理行
严格由 LF 分隔）并自动清理，不依赖 sample.jsonl 等仓库样例或外部服务。

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

from log_viewer import iter_matches

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 过深嵌套层数：远高于各实现的递归保护阈值（见模块文档），保证标准库
# json.loads 抛出 RecursionError；测试不调整解释器递归上限。
DEEP_DEPTH = 100_000
# 对照组的较浅嵌套：同一模板、同样保留 ERROR 级别，但可正常解码。
SHALLOW_DEPTH = 50

# 五个物理行的正文（不含行分隔符）：
# 第 1 行：有效 ERROR；
# 第 2 行：顶层 level 为 ERROR、payload 为过深嵌套数组的完整 JSON 对象，
#          括号完整配对，整个对象只占一行；
# 第 3 行：空行（占物理行号，但不产生警告）；
# 第 4 行：有效但级别不匹配的 INFO；
# 第 5 行：有效 ERROR。
LINE1 = '{"level":"ERROR","message":"before"}'
DEEP_LINE = (
    '{"level":"ERROR","payload":'
    + "[" * DEEP_DEPTH
    + "]" * DEEP_DEPTH
    + "}"
)
LINE3 = ""
LINE4 = '{"level":"INFO"}'
LINE5 = '{"level":"ERROR","message":"after"}'
FIVE_LINES = [LINE1, DEEP_LINE, LINE3, LINE4, LINE5]

# 对照组：仅把第 2 行替换为可正常解码的较浅嵌套对象，其余四行完全相同。
SHALLOW_LINE = (
    '{"level":"ERROR","payload":'
    + "[" * SHALLOW_DEPTH
    + "]" * SHALLOW_DEPTH
    + "}"
)
SHALLOW_FIVE_LINES = [LINE1, SHALLOW_LINE, LINE3, LINE4, LINE5]

# --level ERROR 逐行模式：标准输出严格只有第 1、5 行，原行号 + 制表符 +
# 原始正文，顺序不变；标准错误严格只有第 2 行的一条警告（末尾换行）。
EXPECTED_STDOUT = "1\t" + LINE1 + "\n" + "5\t" + LINE5 + "\n"
EXPECTED_STDERR = "第 2 行：无效日志：JSON 解析失败\n"

# --summary 模式：标准输出只含一个 JSON 摘要和末尾换行。
EXPECTED_SUMMARY_STDOUT = (
    '{"matched_count": 2, "invalid_count": 1, '
    '"by_level": {"DEBUG": 0, "INFO": 0, "WARNING": 0, '
    '"ERROR": 2, "CRITICAL": 0}}\n'
)


def run_cli(*args):
    """在项目根目录下运行 ``python -m log_viewer``，返回完成的进程。"""
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定；
    # 不设置任何递归相关环境变量，保持解释器默认设置。
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
    """过深嵌套单行记录触发 RecursionError 时不中断整个文件处理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, lines) -> str:
        # 以字节写盘并显式使用 LF：五个物理行严格由 LF 分隔，
        # 不经过平台文本模式换行翻译。
        path = Path(self._tmp.name) / "deep_nesting.jsonl"
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        return str(path)

    def test_standard_library_json_loads_raises_recursion_error(self):
        """前提自检：标准库在相同解释器设置下对第 2 行抛 RecursionError。

        既不损坏语法也不模拟异常：该对象只占一行、方括号完整配对，
        同一模板减少嵌套层数即可正常解码；这里也不调用
        sys.setrecursionlimit，与命令行子进程使用同一解释器默认设置。
        """
        # 过深行必须是单行且括号配对的完整 JSON 对象。
        self.assertNotIn("\n", DEEP_LINE)
        self.assertEqual(DEEP_LINE.count("["), DEEP_DEPTH)
        self.assertEqual(DEEP_LINE.count("]"), DEEP_DEPTH)
        self.assertTrue(DEEP_LINE.startswith('{"level":"ERROR","payload":'))
        self.assertTrue(DEEP_LINE.endswith("]}"))
        # 标准库 json.loads 对它抛出的正是 RecursionError
        # （RecursionError 不是 ValueError/JSONDecodeError 的子类）。
        with self.assertRaises(RecursionError):
            json.loads(DEEP_LINE)
        # 同一模板的较浅版本可以正常解码并保留 ERROR 级别，
        # 证明过深行靠的是嵌套深度而非语法损坏。
        self.assertEqual(json.loads(SHALLOW_LINE)["level"], "ERROR")

    def test_deep_line_is_skipped_and_later_lines_still_processed(self):
        """逐行模式：跳过过深行并只警告一次，第 1、5 行照常输出。"""
        path = self._write(FIVE_LINES)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        # 行号由 1 跳到 5：第 2 行被跳过，第 3 行空行照占行号，
        # 第 4 行 INFO 不匹配；正文逐字符相等，顺序不变。
        self.assertEqual(proc.stdout, EXPECTED_STDOUT)
        # 标准错误严格只有第 2 行的一条解析失败警告，
        # 不含异常堆栈；空行不产生警告。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        self.assertNotIn("Traceback", proc.stderr)

    def test_summary_counts_deep_line_as_single_invalid(self):
        """--summary：matched_count 2、invalid_count 1，by_level 计数正确。"""
        path = self._write(FIVE_LINES)
        proc = run_cli(path, "--level", "ERROR", "--summary")
        self.assertEqual(proc.returncode, 0)
        # 标准错误与逐行模式完全一致。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        self.assertNotIn("Traceback", proc.stderr)
        # 标准输出只含一个 JSON 摘要及末尾换行（没有其他行或多余换行）。
        self.assertEqual(proc.stdout, EXPECTED_SUMMARY_STDOUT)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["invalid_count"], 1)
        self.assertEqual(
            summary["by_level"],
            {
                "DEBUG": 0,
                "INFO": 0,
                "WARNING": 0,
                "ERROR": 2,
                "CRITICAL": 0,
            },
        )

    def test_shallow_nested_record_still_matches_without_warning(self):
        """对照组：可正常解码的较浅嵌套 ERROR 记录照常匹配且无警告。"""
        path = self._write(SHALLOW_FIVE_LINES)
        proc = run_cli(path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        # 第 1、2、5 行均按原文输出，行号连续、顺序不变。
        self.assertEqual(
            proc.stdout,
            "1\t" + LINE1 + "\n"
            + "2\t" + SHALLOW_LINE + "\n"
            + "5\t" + LINE5 + "\n",
        )

    def test_iter_matches_records_warning_and_keeps_return_contract(self):
        """公开返回约定不变：过深行进入 warnings，matches 只含前后 ERROR。"""
        matches, warnings = iter_matches(FIVE_LINES, "ERROR")
        self.assertEqual(matches, [(1, LINE1), (5, LINE5)])
        self.assertEqual(warnings, [(2, "无效日志：JSON 解析失败")])


if __name__ == "__main__":
    unittest.main()
