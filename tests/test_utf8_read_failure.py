"""文件尾部 UTF-8 编码损坏的读取失败回归测试。

既有用例覆盖了普通逐行模式中的非法字节；本模块补充文件*尾部*编码损坏
这一读取边界：完整前缀中前面已有一条匹配记录（第 1 行 ERROR）和一条
会产生 JSON 解析警告的损坏行（第 2 行 not-json），随后才在文件末尾
追加非法 UTF-8 字节且不补换行。整份文件应在读取阶段解码失败：默认
逐行、--summary、--jsonl 三种输出模式都必须退出 2、标准输出严格为空、
标准错误只有“文件读取失败”信息，不得留下任何部分结果或逐行警告。

另以*未追加损坏字节*的同一前缀作为成功对照，固定三种模式在相同
--level ERROR 下各自的既有输出约定。

只用 Python 3 标准库（unittest / subprocess / tempfile / json），
输入文件一律按字节在临时目录中构造，用例结束自动清理，
不依赖 sample.jsonl 等外部样例，也不访问网络。

从项目根目录执行（与现有 unittest 发现入口一致，文件名符合
test_*.py 即被自动发现）：

    python -m unittest discover -s tests

也可单独运行：

    python -m unittest tests.test_utf8_read_failure
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 三个物理行的原文：第 1 行匹配 ERROR，第 2 行是损坏 JSON，
# 第 3 行是合法但不匹配的 INFO。
LINE1 = '{"level":"ERROR","message":"失败"}'
LINE2 = "not-json"
LINE3 = '{"level":"INFO","message":"正常"}'

# 完整前缀：三个物理行，每行以 LF 结束（第 3 行后同样有 LF）。按字节构造。
PREFIX = (LINE1 + "\n" + LINE2 + "\n" + LINE3 + "\n").encode("utf-8")

# 两种文件尾部编码损坏，均直接接在前缀末尾之后，末尾不追加换行：
# 单个非法字节，以及一个未完成的三字节 UTF-8 序列（缺尾字节）。
CORRUPT_TAILS = {
    "单个非法字节 0xFF": b"\xff",
    "未完成的 UTF-8 序列 0xE4 0xB8": b"\xe4\xb8",
}

# 三种输出模式：默认逐行、--summary、--jsonl；两个开关分别使用，不组合。
MODES = {
    "默认逐行": (),
    "--summary": ("--summary",),
    "--jsonl": ("--jsonl",),
}

# 成功对照下，标准错误只应有第 2 行的 JSON 解析警告及末尾换行。
WARNING_LINE2 = "第 2 行：无效日志：JSON 解析失败\n"


def run_cli(path, *extra):
    """在项目根目录下运行 ``python -m log_viewer``，固定 --level ERROR。"""
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定；
    # 应用自身的中文消息不受这些变量影响。
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [
            sys.executable, "-m", "log_viewer", str(path),
            "--level", "ERROR", *extra,
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


class TrailingUtf8CorruptionTests(unittest.TestCase):
    """文件尾部编码损坏：整份文件解码失败，三种模式均无部分结果与逐行警告。"""

    def test_trailing_corruption_aborts_all_modes_without_partial_output(self):
        for tail_label, tail in CORRUPT_TAILS.items():
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "trailing-corrupt.jsonl"
                # 按字节写入：完整前缀 + 尾部损坏字节，无末尾换行。
                path.write_bytes(PREFIX + tail)
                for mode_label, mode_args in MODES.items():
                    label = f"{tail_label} / {mode_label}"
                    with self.subTest(label):
                        proc = run_cli(path, *mode_args)
                        # 退出码分类：读取失败必须为 2。
                        self.assertEqual(
                            proc.returncode, 2,
                            f"{label}：退出码不是 2（错误分类错误）：{proc.stderr}",
                        )
                        # 部分结果：第 1 行本会匹配，但整份文件解码失败时
                        # 三种模式的标准输出都必须严格为空。
                        self.assertEqual(
                            proc.stdout, "",
                            f"{label}：读取失败后标准输出仍有部分结果："
                            f"{proc.stdout!r}",
                        )
                        # 错误信息只固定关键文案与输入路径，不绑定底层异常
                        # 的完整文案、字节偏移或系统附加细节。
                        self.assertIn(
                            "文件读取失败", proc.stderr,
                            f"{label}：标准错误缺少读取失败标识：{proc.stderr!r}",
                        )
                        self.assertIn(
                            str(path), proc.stderr,
                            f"{label}：标准错误未包含输入路径：{proc.stderr!r}",
                        )
                        # 警告泄漏：第 2 行本会产生 JSON 解析警告，但整份文件
                        # 解码失败时逐行警告不得输出。
                        self.assertNotIn(
                            "无效日志", proc.stderr,
                            f"{label}：读取失败仍泄漏了逐行警告：{proc.stderr!r}",
                        )
                        # 异常必须被捕获并转成读取失败文案，不能冒出堆栈。
                        self.assertNotIn(
                            "Traceback", proc.stderr,
                            f"{label}：标准错误出现未捕获异常的堆栈：{proc.stderr!r}",
                        )


class IntactPrefixControlTests(unittest.TestCase):
    """成功对照：未追加损坏字节的同一前缀，三种模式各自的既有输出约定。"""

    def setUp(self):
        # 与损坏用例完全相同的完整前缀，仅不追加任何损坏字节。
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "intact.jsonl"
        self.path.write_bytes(PREFIX)

    def test_default_line_mode_outputs_first_line_only(self):
        proc = run_cli(self.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 默认逐行：只输出第 1 行的行号、制表符和原文及末尾 LF。
        self.assertEqual(proc.stdout, "1\t" + LINE1 + "\n")
        self.assertEqual(proc.stderr, WARNING_LINE2)

    def test_jsonl_mode_outputs_first_record_only(self):
        proc = run_cli(self.path, "--jsonl")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # JSONL：只输出第一条匹配记录原文及一个 LF，无行号制表符前缀。
        self.assertEqual(proc.stdout, LINE1 + "\n")
        self.assertEqual(proc.stderr, WARNING_LINE2)

    def test_summary_mode_counts_one_match_and_one_invalid(self):
        proc = run_cli(self.path, "--summary")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出只有一个 JSON 对象和一个末尾换行，无多余片段。
        self.assertEqual(proc.stdout.count("\n"), 1)
        self.assertTrue(proc.stdout.endswith("\n"))
        summary = json.loads(proc.stdout)
        # matched_count 与 invalid_count 均为 1；by_level 中 ERROR 为 1，
        # 其余四个级别均为 0，且不存在额外字段。
        self.assertEqual(
            summary,
            {
                "matched_count": 1,
                "invalid_count": 1,
                "by_level": {
                    "DEBUG": 0,
                    "INFO": 0,
                    "WARNING": 0,
                    "ERROR": 1,
                    "CRITICAL": 0,
                },
            },
        )
        self.assertEqual(proc.stderr, WARNING_LINE2)


if __name__ == "__main__":
    unittest.main()
