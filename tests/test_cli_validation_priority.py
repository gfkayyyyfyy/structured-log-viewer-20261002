"""命令行参数校验优先级的回归测试。

现有测试分别覆盖各类参数的单独错误；本模块补充“多个无效参数与不可读
文件同时出现时，用户首先看到哪个错误”的优先级回归：参数解析成功之后、
读取文件之前的校验顺序必须固定为

    1. --jsonl 与 --summary 互斥
    2. --level 级别合法性
    3. --request-id 非空
    4. --message-contains 非空
    5. --since 的 UTC 格式
    6. --until 的 UTC 格式
    7. --since 不晚于 --until
    8. --line-range 重复提供、区间格式、零端点、起点大于终点

全部参数校验通过之后才进入文件读取阶段。

测试方法：用同一条包含多处错误的输入逐步修正参数——

    起初同时提供 --jsonl 和 --summary，级别为 TRACE，请求标识为空字符串，
    消息候选为三个空格，--since 和 --until 都为 bad，文件路径确定不存在，
    此时只报告两个输出开关不能同时使用；
    移除 --jsonl 后只报告级别错误；
    将级别改为 ERROR 后只报告请求标识不能为空；
    将标识改为 r-1 后只报告消息候选不能为空；
    将候选改为 timeout 后只报告 --since 的 UTC 格式错误；
    将起点改为 2026-10-03T10:00:01Z 后只报告 --until 的格式错误；
    将终点改为 2026-10-03T10:00:00Z 后只报告起点不能晚于终点。

上述每一步都要求：退出码为 2，标准输出严格为空，标准错误只有当前源码
对应的一条参数错误和末尾换行——不出现后续参数错误、文件读取失败或
“无效日志”警告（标准错误按字节精确比对，天然排除任何额外输出）。
空值（空字符串、三个空格）作为真实参数值经参数列表传入，所有选项均有值。

另验证：
- 调整不同选项在命令行中的位置（含把位置参数放到末尾）不改变上述优先级；
- 把终点改为与起点相等（合法空区间）不会提前结束校验流程，而是因路径
  不存在返回退出码 2，标准输出为空，标准错误包含“文件读取失败”和该
  路径（系统异常细节不作固定要求）；
- 用临时文件中的非法 UTF-8 字节复核输出开关互斥这一最高优先级：即使
  文件读取必然失败，结果仍只有互斥错误，以此区分参数拒绝与文件读取错误。

行号区间（优先级最低的一类参数校验）与其他错误并存的覆盖：

- 对不存在的路径同时传入 --level TRACE、--jsonl、--summary 和
  --line-range 0:2 时只出现互斥错误；去掉两个导出开关后只出现级别错误，
  即行号区间的零端点错误不会越过更靠前的规则暴露；
- 其余参数均合法而 --line-range 重复提供且其中一次为非法值时，
  只出现重复提供错误（重复检查先于格式、零端点和起止关系检查）；
- 合法 ERROR 级别、相等的起止时间和合法行号区间通过全部校验，
  随后因路径不存在返回退出码 2，标准输出为空，标准错误报告文件读取
  失败且不含任何参数错误。

测试数据与不存在的路径均由用例在临时目录中准备并自动清理，不依赖仓库
样例（sample.jsonl 等）或外部服务；比较按原始字节进行（不开 text=True），
记录终止换行沿用运行平台现有行为（os.linesep）。产品代码、公开接口、
筛选语义以及三种输出格式均不改动。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# print 写标准错误时的平台记录终止换行，按字节比较；
# 子进程文本层的换行翻译随当前平台，不做归一化。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 当前源码中各参数错误的完整文案（__main__.py 中的 print 输出）。
ERROR_MUTEX = "参数错误：--jsonl 与 --summary 不能同时使用"
ERROR_LEVEL = (
    "参数错误：--level 必须是以下级别之一："
    "DEBUG, INFO, WARNING, ERROR, CRITICAL"
)
ERROR_REQUEST_ID = "参数错误：--request-id 不能为空或全为空白"
ERROR_MESSAGE_CONTAINS = "参数错误：--message-contains 不能为空或全为空白"
ERROR_SINCE = "参数错误：--since 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间"
ERROR_UNTIL = "参数错误：--until 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间"
ERROR_ORDER = "参数错误：--since 不能晚于 --until"
ERROR_LINE_RANGE_DUPLICATE = "参数错误：--line-range 只能提供一次"

READ_FAILURE_MESSAGE = "文件读取失败".encode("utf-8")
INVALID_LOG_WARNING = "无效日志".encode("utf-8")
TRACEBACK = b"Traceback"

# 逐步修正链：每一步在上一处修正的基础上只再改一处，期望错误随之推进。
# 每项为 (步骤说明, 相对上一步的改动, 期望的唯一标准错误文案)。
# 改动以 (选项, 新值) 表示；新值为 None 表示移除该选项。
INITIAL_OPTIONS = [
    ("--jsonl", True),
    ("--summary", True),
    ("--level", "TRACE"),
    ("--request-id", ""),
    ("--message-contains", "   "),
    ("--since", "bad"),
    ("--until", "bad"),
]

FIXUP_CHAIN = [
    ("全部参数均无效：只报告输出开关互斥", [], ERROR_MUTEX),
    ("移除 --jsonl：只报告级别错误", [("--jsonl", None)], ERROR_LEVEL),
    ("级别改为 ERROR：只报告请求标识不能为空",
     [("--level", "ERROR")], ERROR_REQUEST_ID),
    ("标识改为 r-1：只报告消息候选不能为空",
     [("--request-id", "r-1")], ERROR_MESSAGE_CONTAINS),
    ("候选改为 timeout：只报告 --since 格式错误",
     [("--message-contains", "timeout")], ERROR_SINCE),
    ("起点改为 2026-10-03T10:00:01Z：只报告 --until 格式错误",
     [("--since", "2026-10-03T10:00:01Z")], ERROR_UNTIL),
    ("终点改为 2026-10-03T10:00:00Z：只报告起点不能晚于终点",
     [("--until", "2026-10-03T10:00:00Z")], ERROR_ORDER),
]


def build_argv(path, options):
    """把 (选项, 值) 列表展开为完整命令行：位置参数在前，各选项跟值。

    值为 True 的开关只写选项名；空字符串与纯空白值原样作为真实参数传入。
    """
    argv = [str(path)]
    for name, value in options:
        argv.append(name)
        if value is not True:
            argv.append(value)
    return argv


def apply_edits(options, edits):
    """在选项列表上应用一处修正：新值为 None 表示移除该选项。"""
    result = list(options)
    for name, value in edits:
        result = [pair for pair in result if pair[0] != name]
        if value is not None:
            result.append((name, value))
    return result


def run_cli_bytes(argv):
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
        [sys.executable, "-m", "log_viewer", *argv],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class ValidationPriorityTests(unittest.TestCase):
    """多处错误同时存在时，逐步修正链上每一步只暴露当前最高优先级的错误。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 确定不存在的路径：临时目录内不创建该文件。
        self.missing_path = Path(self._tmp.name) / "missing.jsonl"
        self.assertFalse(self.missing_path.exists())

    def test_fixup_chain_reports_one_error_at_a_time(self):
        options = INITIAL_OPTIONS
        for step_label, edits, expected_message in FIXUP_CHAIN:
            options = apply_edits(options, edits)
            argv = build_argv(self.missing_path, options)
            with self.subTest(步骤=step_label, argv=argv):
                proc = run_cli_bytes(argv)

                # 1) 错误分类：参数错误退出码为 2。
                self.assertEqual(
                    proc.returncode, 2,
                    f"应退出 2，实际 {proc.returncode}；argv={argv!r}；"
                    f"stderr={proc.stderr!r}",
                )

                # 2) 标准输出严格为空：参数校验在读取文件之前拒绝。
                self.assertEqual(
                    proc.stdout, b"",
                    f"参数错误时标准输出必须为空，实际 {proc.stdout!r}",
                )

                # 3) 标准错误按字节精确等于当前一条参数错误加末尾换行：
                #    不出现后续参数错误、文件读取失败或“无效日志”警告。
                self.assertEqual(
                    proc.stderr,
                    expected_message.encode("utf-8") + RECORD_TERMINATOR,
                    f"步骤「{step_label}」的标准错误应只有一条参数错误；"
                    f"argv={argv!r}",
                )

    def test_option_order_does_not_change_priority(self):
        """选项在命令行中的位置不影响校验优先级。"""
        # 全错输入的另一种排布：位置参数放最后，选项顺序整体颠倒。
        shuffled_all_bad = [
            "--until", "bad",
            "--since", "bad",
            "--message-contains", "   ",
            "--request-id", "",
            "--level", "TRACE",
            "--summary",
            "--jsonl",
            str(self.missing_path),
        ]
        # 中间一步（只应报告 --since 格式错误）的另一种排布。
        shuffled_since_step = [
            "--message-contains", "timeout",
            "--until", "bad",
            str(self.missing_path),
            "--since", "bad",
            "--request-id", "r-1",
            "--level", "ERROR",
            "--summary",
        ]
        cases = [
            ("全错输入重排后仍只报互斥", shuffled_all_bad, ERROR_MUTEX),
            ("中间步骤重排后仍只报 --since", shuffled_since_step, ERROR_SINCE),
        ]
        for label, argv, expected_message in cases:
            with self.subTest(场景=label, argv=argv):
                proc = run_cli_bytes(argv)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(proc.stdout, b"")
                self.assertEqual(
                    proc.stderr,
                    expected_message.encode("utf-8") + RECORD_TERMINATOR,
                )

    def test_equal_since_until_is_valid_and_reaches_file_read(self):
        """起点与终点相等是合法空区间：校验通过后因路径不存在而读取失败。"""
        argv = build_argv(
            self.missing_path,
            [
                ("--summary", True),
                ("--level", "ERROR"),
                ("--request-id", "r-1"),
                ("--message-contains", "timeout"),
                ("--since", "2026-10-03T10:00:00Z"),
                ("--until", "2026-10-03T10:00:00Z"),
            ],
        )
        proc = run_cli_bytes(argv)

        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；stderr={proc.stderr!r}",
        )
        self.assertEqual(proc.stdout, b"")
        # 标准错误包含“文件读取失败”与该路径；系统异常细节不作固定要求。
        self.assertIn(READ_FAILURE_MESSAGE, proc.stderr)
        self.assertIn(os.fsencode(str(self.missing_path)), proc.stderr)
        # 不得再出现任何参数错误：合法空区间不会提前结束校验流程。
        self.assertNotIn("参数错误".encode("utf-8"), proc.stderr)
        self.assertNotIn(INVALID_LOG_WARNING, proc.stderr)
        self.assertNotIn(TRACEBACK, proc.stderr)

    def test_mutex_outranks_undecodable_file(self):
        """非法 UTF-8 文件 + 输出开关互斥：仍只报告互斥这一最高优先级。"""
        corrupt_path = Path(self._tmp.name) / "corrupt.jsonl"
        # 单个非法字节 0xFF：整份文件 UTF-8 解码必然失败。
        corrupt_path.write_bytes(b'{"level":"ERROR"}\n\xff')
        argv = build_argv(
            corrupt_path,
            [
                ("--jsonl", True),
                ("--summary", True),
                ("--level", "ERROR"),
                ("--request-id", "r-1"),
                ("--message-contains", "timeout"),
                ("--since", "2026-10-03T10:00:00Z"),
                ("--until", "2026-10-03T10:00:01Z"),
            ],
        )
        proc = run_cli_bytes(argv)

        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；stderr={proc.stderr!r}",
        )
        self.assertEqual(proc.stdout, b"")
        # 参数拒绝优先于文件读取：只有互斥错误，没有文件读取失败。
        self.assertEqual(
            proc.stderr,
            ERROR_MUTEX.encode("utf-8") + RECORD_TERMINATOR,
        )


class LineRangeValidationPriorityTests(unittest.TestCase):
    """行号区间校验与其他参数错误并存时，仍按固定优先级只报最先的问题。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 确定不存在的路径：临时目录内不创建该文件。
        self.missing_path = Path(self._tmp.name) / "missing.jsonl"
        self.assertFalse(self.missing_path.exists())

    def assert_single_error(self, argv, expected_message):
        """公共断言：退出码 2、标准输出为空、标准错误恰为一条参数错误。"""
        proc = run_cli_bytes(argv)
        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；argv={argv!r}；"
            f"stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stdout, b"",
            f"参数错误时标准输出必须为空，实际 {proc.stdout!r}",
        )
        self.assertEqual(
            proc.stderr,
            expected_message.encode("utf-8") + RECORD_TERMINATOR,
            f"标准错误应只有一条参数错误；argv={argv!r}",
        )

    def test_mutex_outranks_level_and_line_range_errors(self):
        """互斥、级别非法、行号区间零端点并存：只报告输出开关互斥。"""
        argv = build_argv(
            self.missing_path,
            [
                ("--jsonl", True),
                ("--summary", True),
                ("--level", "TRACE"),
                ("--line-range", "0:2"),
            ],
        )
        self.assert_single_error(argv, ERROR_MUTEX)

    def test_level_outranks_line_range_error(self):
        """去掉导出开关后：级别非法优先于行号区间的零端点错误。"""
        argv = build_argv(
            self.missing_path,
            [
                ("--level", "TRACE"),
                ("--line-range", "0:2"),
            ],
        )
        self.assert_single_error(argv, ERROR_LEVEL)

    def test_duplicate_line_range_reported_before_value_checks(self):
        """其余参数合法、行号区间重复提供且含非法值：只报告重复提供。"""
        argv = build_argv(
            self.missing_path,
            [
                ("--level", "ERROR"),
                ("--request-id", "r-1"),
                ("--message-contains", "timeout"),
                ("--since", "2026-10-03T10:00:00Z"),
                ("--until", "2026-10-03T10:00:01Z"),
                ("--line-range", "1:2"),
                ("--line-range", "bad"),
            ],
        )
        self.assert_single_error(argv, ERROR_LINE_RANGE_DUPLICATE)

    def test_valid_line_range_reaches_file_read(self):
        """合法级别、相等起止与合法行号区间通过校验，随后读取失败。"""
        argv = build_argv(
            self.missing_path,
            [
                ("--level", "ERROR"),
                ("--since", "2026-10-03T10:00:00Z"),
                ("--until", "2026-10-03T10:00:00Z"),
                ("--line-range", "1:2"),
            ],
        )
        proc = run_cli_bytes(argv)
        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；stderr={proc.stderr!r}",
        )
        self.assertEqual(proc.stdout, b"")
        # 标准错误包含“文件读取失败”与该路径；系统异常细节不作固定要求。
        self.assertIn(READ_FAILURE_MESSAGE, proc.stderr)
        self.assertIn(os.fsencode(str(self.missing_path)), proc.stderr)
        # 不得再出现任何参数错误：合法参数不会提前结束校验流程。
        self.assertNotIn("参数错误".encode("utf-8"), proc.stderr)
        self.assertNotIn(INVALID_LOG_WARNING, proc.stderr)
        self.assertNotIn(TRACEBACK, proc.stderr)


if __name__ == "__main__":
    unittest.main()
