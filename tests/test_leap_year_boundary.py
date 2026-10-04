"""闰年日历边界的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均在用例自建的临时目录中生成并在用例结束时清理，
不依赖 sample.jsonl、cr.jsonl 或外部服务。

覆盖三组行为：
1. 2000 年是闰年（能被 400 整除）：2000-02-29 全天为合法时间，
   含起点、不含终点的区间只命中闰日内的记录；
2. 1900 年与 2100 年不是闰年（能被 100 整除但不能被 400 整除）：
   当年的 2 月 29 日在记录中是非法 timestamp（产生警告），
   作为 --since / --until 参数则在读取文件前被拒绝；
3. 不传 --since / --until 时不做日期检查，非法日期记录照常输出。

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

# 闰年边界的六个物理行：
# 第 1 行在闰日前一秒（区间外），第 2 行恰在起点（含起点，输出），
# 第 3 行恰在终点（不含终点，静默跳过），
# 第 4 行级别不匹配但日期非法（1900 非闰年，仍检查并警告），
# 第 5 行日期非法（2100 非闰年，警告），
# 第 6 行在闰日末尾（区间内，输出）。
LEAP_LINES = [
    '{"level":"ERROR","timestamp":"2000-02-28T23:59:59Z"}',
    '{"level":"ERROR","timestamp":"2000-02-29T00:00:00Z"}',
    '{"level":"ERROR","timestamp":"2000-03-01T00:00:00Z"}',
    '{"level":"INFO","timestamp":"1900-02-29T12:00:00Z"}',
    '{"level":"ERROR","timestamp":"2100-02-29T12:00:00Z"}',
    '{"level":"ERROR","timestamp":"2000-02-29T23:59:59Z"}',
]
# 第 4、5 行的非法日期值，也用作 --since / --until 的非法参数。
INVALID_1900 = "1900-02-29T12:00:00Z"
INVALID_2100 = "2100-02-29T12:00:00Z"


def run_cli(*args):
    """在项目根目录下运行 ``python -m log_viewer``，返回完成的进程。"""
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
        text=True,
        encoding="utf-8",
        env=env,
    )


class LeapYearBoundaryTests(unittest.TestCase):
    """2000 年闰日区间的命令行行为：匹配行、警告、退出码分别断言。"""

    def _write_leap_log(self, tmpdir) -> str:
        path = Path(tmpdir) / "leap.jsonl"
        path.write_text("\n".join(LEAP_LINES) + "\n", encoding="utf-8")
        return str(path)

    def test_leap_day_half_open_interval(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(
                self._write_leap_log(d), "--level", "ERROR",
                "--since", "2000-02-29T00:00:00Z",
                "--until", "2000-03-01T00:00:00Z",
            )
        # 退出码、匹配行、警告分别断言，失败时能区分三者差异。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 2、6 行：保留原始行号、制表符与原文，顺序不变。
        self.assertEqual(
            proc.stdout,
            "2\t" + LEAP_LINES[1] + "\n"
            "6\t" + LEAP_LINES[5] + "\n",
        )
        # 第 4 行级别不匹配仍检查日期；第 4、5 行各只有一条警告，按行号顺序。
        self.assertEqual(
            proc.stderr,
            "第 4 行：无效日志：timestamp 缺失或格式无效\n"
            "第 5 行：无效日志：timestamp 缺失或格式无效\n",
        )

    def test_invalid_leap_dates_rejected_as_arguments_before_reading_file(self):
        # 第 4、5 行的非法日期分别作为 --since 与 --until，共四种情况；
        # 路径在临时目录中确定不存在，用于证明参数校验先于文件读取。
        cases = [
            ("--since", INVALID_1900),
            ("--until", INVALID_1900),
            ("--since", INVALID_2100),
            ("--until", INVALID_2100),
        ]
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            for option, value in cases:
                with self.subTest(f"{option} {value}"):
                    proc = run_cli(
                        missing, "--level", "ERROR", option, value
                    )
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn(option, proc.stderr)
                    # 不读取文件：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)

    def test_without_time_options_no_date_check(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_cli(self._write_leap_log(d), "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 日期检查只在时间筛选启用后生效：不传时间选项时第 1、2、3、5、6 行
        #（全部 ERROR 记录）照常输出，包括日期非法的第 5 行。
        self.assertEqual(
            proc.stdout,
            "1\t" + LEAP_LINES[0] + "\n"
            "2\t" + LEAP_LINES[1] + "\n"
            "3\t" + LEAP_LINES[2] + "\n"
            "5\t" + LEAP_LINES[4] + "\n"
            "6\t" + LEAP_LINES[5] + "\n",
        )
        self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
