"""--message-excludes 消息子串排除的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile），
测试输入均由各用例在临时目录中以 UTF-8 自行生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、demo.jsonl、网络或外部服务；
产品代码、其他筛选、摘要、导出与 iter_matches 返回约定保持不变。

覆盖要点（标准输出、标准错误、退出码分别断言，失败信息可定位差异）：
- 固定样例连续八行均为合法 ERROR 对象，message 依次为 timeout、
  timeout retry、Timeout、retry、缺失、null、数字 42，以及只在 nested
  中出现 timeout 而顶层缺失。选择 ERROR 并排除 timeout、retry 后，
  只输出原始第 3、5、6、7、8 行，保持文件顺序与正文，每行仍由
  “原行号<Tab>原文”组成；标准错误为空，退出码为 0。
- 交换两个排除值的顺序或重复其中某个值，输出完全相同；不传排除选项时
  八行全部输出。
- 同一文件改用 --message-contains timeout 且 --message-excludes retry：
  包含与排除取交集，只输出第 1 行。
- 小样例验证 ``a.*b`` 按普通文字排除（不当作正则），axb 行保留；
  合法排除值的首尾空白参与匹配，不被裁剪。
- 四行诊断样例（合法 keep / timestamp 为 null 的 timeout / not-json /
  合法 timeout）配合 --since 与排除 timeout：只输出第 1 行；标准错误
  依次输出第 2 行 timestamp 诊断与第 3 行 JSON 解析诊断，每行一条并带
  原行号；退出码为 0。
- 排除参数为空字符串或全空白：即使另有合法值且路径不存在，也只报告
  “参数错误：--message-excludes 不能为空或全为空白”，标准输出为空，
  退出码为 2（在读取文件之前拒绝）。

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

# ---------------------------------------------------------------------------
# 固定样例的 8 个物理行（不含行分隔符），全部为合法 ERROR JSON 对象。
# 行号在样例中固定，断言显式写出预期行号，不随行序变化隐式推导。
# ---------------------------------------------------------------------------
LINE1 = '{"level":"ERROR","message":"timeout"}'
LINE2 = '{"level":"ERROR","message":"timeout retry"}'
# 第 3 行首字母大写：区分大小写的排除不应命中 "timeout" 或 "retry"。
LINE3 = '{"level":"ERROR","message":"Timeout"}'
LINE4 = '{"level":"ERROR","message":"retry"}'
# 第 5 行顶层 message 缺失。
LINE5 = '{"level":"ERROR"}'
# 第 6 行 message 为 null。
LINE6 = '{"level":"ERROR","message":null}'
# 第 7 行 message 为数字。
LINE7 = '{"level":"ERROR","message":42}'
# 第 8 行 timeout 只出现在嵌套对象中，顶层 message 缺失。
LINE8 = '{"level":"ERROR","nested":{"message":"timeout"}}'
FIXTURE_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7, LINE8]
F_PAYLOAD = ("\n".join(FIXTURE_LINES) + "\n").encode("utf-8")

# 排除 timeout、retry 后只保留第 3、5、6、7、8 行：
# 第 1 行命中 timeout、第 2 行同时命中两者、第 4 行命中 retry，均被剔除；
# 第 3 行大小写不同不命中；缺失/null/数字/仅嵌套的行不因排除丢弃。
EXCLUDE_BOTH_STDOUT = "".join(
    f"{i}\t{FIXTURE_LINES[i - 1]}\n" for i in (3, 5, 6, 7, 8)
)

# --message-contains timeout 且 --message-excludes retry：
# 只有第 1 行同时满足“包含 timeout 且不含 retry”。
CONTAINS_TIMEOUT_EXCLUDE_RETRY_STDOUT = f"1\t{LINE1}\n"

# 四行诊断样例使用的合法时刻与时间参数。
SINCE_TS = "2026-10-03T10:00:00Z"


def _numbered(lines, *indices):
    """按给定行号（从 1 开始）拼成 ``行号<Tab>原文`` 的预期标准输出。"""
    return "".join(f"{i}\t{lines[i - 1]}\n" for i in indices)


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


class MessageExcludesFilterTests(unittest.TestCase):
    """八行固定样例上的 --message-excludes 匹配语义与顺序无关性。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "messages.jsonl"
        path.write_bytes(F_PAYLOAD)
        self.path = str(path)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：8 个物理行均为合法对象，行号约定成立。
        self.assertEqual(len(FIXTURE_LINES), 8)
        self.assertEqual(F_PAYLOAD.count(b"\n"), 8)
        self.assertEqual(FIXTURE_LINES[4], '{"level":"ERROR"}')
        self.assertEqual(FIXTURE_LINES[5], '{"level":"ERROR","message":null}')
        self.assertEqual(FIXTURE_LINES[6], '{"level":"ERROR","message":42}')
        self.assertEqual(
            FIXTURE_LINES[7],
            '{"level":"ERROR","nested":{"message":"timeout"}}',
        )

    def test_excluding_timeout_and_retry_keeps_other_five_lines(self):
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-excludes", "timeout",
            "--message-excludes", "retry",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出原始第 3、5、6、7、8 行，保持文件顺序与原文，
        # 每行仍为“原行号<Tab>原文”。
        self.assertEqual(proc.stdout, EXCLUDE_BOTH_STDOUT)
        self.assertEqual(proc.stderr, "")

    def test_exclude_value_order_swap_does_not_change_output(self):
        # 交换两个排除值的顺序：输出与上面逐字节相同。
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-excludes", "retry",
            "--message-excludes", "timeout",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXCLUDE_BOTH_STDOUT)
        self.assertEqual(proc.stderr, "")

    def test_duplicate_exclude_values_do_not_change_output(self):
        # 重复某个（或两个）排除值：输出完全相同，匹配行不重复输出。
        for values in (
            ("timeout", "timeout", "retry"),
            ("retry", "retry", "timeout"),
            ("timeout", "retry", "timeout", "retry"),
        ):
            with self.subTest(values):
                args = [self.path, "--level", "ERROR"]
                for value in values:
                    args += ["--message-excludes", value]
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, EXCLUDE_BOTH_STDOUT)
                self.assertEqual(proc.stderr, "")

    def test_without_exclude_option_all_eight_lines_output(self):
        # 不传 --message-excludes：八行全部按 ERROR 级别输出，无诊断。
        proc = run_cli(self.path, "--level", "ERROR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout, "".join(f"{i}\t{FIXTURE_LINES[i - 1]}\n" for i in range(1, 9))
        )
        self.assertEqual(proc.stderr, "")

    def test_contains_timeout_and_excludes_retry_intersect_to_first_line(self):
        # 同一文件：包含 timeout 且排除 retry。第 2 行虽含 timeout 但同时
        # 含 retry 被剔除；第 3 行大小写不同不满足包含；只剩第 1 行。
        proc = run_cli(
            self.path, "--level", "ERROR",
            "--message-contains", "timeout",
            "--message-excludes", "retry",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, CONTAINS_TIMEOUT_EXCLUDE_RETRY_STDOUT)
        self.assertEqual(proc.stderr, "")


class MessageExcludesLiteralAndSpacingTests(unittest.TestCase):
    """元字符按普通文字排除；合法排除值的首尾空白参与匹配。"""

    def test_metacharacters_are_literal_when_excluding(self):
        lines = [
            '{"level":"ERROR","message":"a.*b"}',
            '{"level":"ERROR","message":"axb"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "literal.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # a.*b 按普通文字排除：只剔除第 1 行；若被当作正则，
            # 第 2 行 axb 也会被 .* 匹配而错误剔除。
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--message-excludes", "a.*b",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(lines, 2))
        self.assertEqual(proc.stderr, "")

    def test_valid_exclude_value_surrounding_spaces_participate_in_match(self):
        lines = [
            # 1 无首尾空格：不包含子串“ timeout ”，保留。
            '{"level":"ERROR","message":"timeout"}',
            # 2 解码后首尾带空格：被“ timeout ”精确命中，剔除。
            '{"level":"ERROR","message":" timeout "}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spaces.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # 排除值的首尾空白是检索文字的一部分，不被裁剪。
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--message-excludes", " timeout ",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _numbered(lines, 1))
        self.assertEqual(proc.stderr, "")
        # 反向确认：去掉排除值的首尾空格后两行都含 timeout，均被剔除。
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spaces2.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--message-excludes", "timeout",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")


class MessageExcludesDiagnosticsTests(unittest.TestCase):
    """--since 启用时无效行诊断边界不被排除条件遮蔽。"""

    def test_since_with_exclude_reports_timestamp_and_parse_warnings(self):
        lines = [
            # 1 合法 ERROR、时刻即起点、消息 keep：唯一输出。
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"keep"}',
            # 2 timestamp 为 null：先产出 timestamp 诊断（先于消息排除判断）。
            '{"level":"ERROR","timestamp":null,"message":"timeout"}',
            # 3 不是 JSON：解析失败诊断。
            "not-json",
            # 4 合法但消息命中排除值：静默剔除，无诊断。
            '{"level":"ERROR","timestamp":"2026-10-03T10:00:00Z","message":"timeout"}',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "diagnostics.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            proc = run_cli(
                str(path), "--level", "ERROR",
                "--since", SINCE_TS,
                "--message-excludes", "timeout",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只输出第 1 行，保留原行号、制表符与原文。
        self.assertEqual(proc.stdout, _numbered(lines, 1))
        # 标准错误依次为第 2 行 timestamp 诊断、第 3 行解析失败诊断，
        # 各带原行号，每行一条；第 4 行被排除不产生任何输出。
        self.assertEqual(
            proc.stderr,
            "第 2 行：无效日志：timestamp 缺失或格式无效\n"
            "第 3 行：无效日志：JSON 解析失败\n",
        )


class MessageExcludesArgumentTests(unittest.TestCase):
    """空或全空白排除值：退出码 2、标准输出为空，且先于读文件校验。"""

    def test_blank_value_rejected_before_reading_file_with_exact_message(self):
        # 路径不存在也不读取：即使另有合法排除值，标准错误也只有一条
        # 参数错误，不出现文件读取失败；标准输出为空，退出码为 2。
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "no-such-file.jsonl")
            cases = [
                ("",),
                ("   ",),
                ("\t",),
                (" \t ",),
                # 全空白值即使排在合法值之后，也不能被合法值覆盖。
                ("timeout", "  "),
                # 全空白值排在合法值之前同样拒绝。
                (" \t", "retry"),
            ]
            for values in cases:
                with self.subTest(values=values):
                    args = [missing, "--level", "ERROR"]
                    for value in values:
                        args += ["--message-excludes", value]
                    proc = run_cli(*args)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, "")
                    self.assertEqual(
                        proc.stderr,
                        "参数错误：--message-excludes 不能为空或全为空白\n",
                    )


if __name__ == "__main__":
    unittest.main()
