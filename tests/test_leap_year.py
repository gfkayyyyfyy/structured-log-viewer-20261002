"""闰年日历边界的离线回归测试：--since/--until 时间筛选。

只用 Python 3 标准库（unittest / subprocess / tempfile / pathlib），
测试输入均在用例自建的临时目录中生成并自动清理，
不依赖 sample.jsonl 或外部服务；不涉及时区、小数秒、摘要或导出模式。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例，
失败信息可区分匹配行、警告与退出码的差异。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 六个物理行，覆盖闰年日历边界：
# 第 1 行：闰日前一秒（2000 是 400 的倍数，为闰年），早于起点静默跳过；
# 第 2 行：闰日零点，恰为含起点，输出；
# 第 3 行：恰为不含终点，静默跳过；
# 第 4 行：1900 是 100 的倍数但非 400 的倍数，不是闰年，日期不存在；
#          级别为 INFO 不匹配，但启用时间筛选后仍检查日期，产生一条警告；
# 第 5 行：2100 同样不是闰年，日期不存在，产生一条警告；
# 第 6 行：闰日最后一秒，在区间内，输出。
LEAP_LINES = [
    '{"level":"ERROR","timestamp":"2000-02-28T23:59:59Z"}',
    '{"level":"ERROR","timestamp":"2000-02-29T00:00:00Z"}',
    '{"level":"ERROR","timestamp":"2000-03-01T00:00:00Z"}',
    '{"level":"INFO","timestamp":"1900-02-29T12:00:00Z"}',
    '{"level":"ERROR","timestamp":"2100-02-29T12:00:00Z"}',
    '{"level":"ERROR","timestamp":"2000-02-29T23:59:59Z"}',
]
# 第 4、5 行中不存在的日期值，同时用作非法的 --since/--until 参数。
INVALID_DATE_1900 = "1900-02-29T12:00:00Z"
INVALID_DATE_2100 = "2100-02-29T12:00:00Z"


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
    """跨闰日 [2000-02-29, 2000-03-01) 的含起点、不含终点区间。"""

    def _run(self, tmpdir, *extra):
        path = Path(tmpdir) / "leap.jsonl"
        path.write_text("\n".join(LEAP_LINES) + "\n", encoding="utf-8")
        return run_cli(str(path), "--level", "ERROR", *extra)

    def test_interval_over_leap_day_outputs_lines_2_and_6(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(
                d,
                "--since", "2000-02-29T00:00:00Z",
                "--until", "2000-03-01T00:00:00Z",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只含第 2、6 行：保留原始行号、制表符与原文，顺序不变。
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

    def test_without_time_options_no_date_check(self):
        # 不传时间选项：日期检查不生效，第 5 行的非法日期照常输出，
        # 第 4 行仅因级别不匹配而静默跳过，无任何警告。
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(d)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "1\t" + LEAP_LINES[0] + "\n"
            "2\t" + LEAP_LINES[1] + "\n"
            "3\t" + LEAP_LINES[2] + "\n"
            "5\t" + LEAP_LINES[4] + "\n"
            "6\t" + LEAP_LINES[5] + "\n",
        )
        self.assertEqual(proc.stderr, "")


class LeapYearArgumentErrorTests(unittest.TestCase):
    """不存在的闰日作为 --since/--until 参数：读取文件前被拒绝。"""

    def test_invalid_leap_dates_rejected_before_reading_file(self):
        cases = [
            ("--since", INVALID_DATE_1900),
            ("--until", INVALID_DATE_1900),
            ("--since", INVALID_DATE_2100),
            ("--until", INVALID_DATE_2100),
        ]
        with tempfile.TemporaryDirectory() as d:
            # 路径确定不存在：若实现先读文件会报“文件读取失败”而非参数错误。
            missing = str(Path(d) / "no-such-file.jsonl")
            for option, value in cases:
                with self.subTest(option=option, value=value):
                    proc = run_cli(
                        missing, "--level", "ERROR", option, value
                    )
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn("参数错误", proc.stderr)
                    self.assertIn(option, proc.stderr)
                    # 不读取文件：不会报文件读取失败。
                    self.assertNotIn("文件读取失败", proc.stderr)


if __name__ == "__main__":
    unittest.main()
