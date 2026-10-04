"""命令行参数校验优先级的回归测试。

现有测试分别覆盖各类参数的单独错误；本模块补充“多个无效参数与不可读
文件同时出现时，用户首先看到哪个错误”的优先级回归：参数解析成功之后、
读取文件之前的校验流程必须按源码中的固定顺序报告第一条错误，不得越过
它报告后续参数错误、文件读取失败或逐行解析阶段的“无效日志”警告。

校验优先级（与 log_viewer/__main__.py 中的检查顺序一致）：
    1. --jsonl 与 --summary 互斥
    2. --level 级别合法性
    3. --request-id 不能为空或全为空白
    4. --message-contains 不能为空或全为空白
    5. --since 的 UTC 格式
    6. --until 的 UTC 格式
    7. --since 不能晚于 --until（起止相等是合法空区间）
    8. 以上全部通过后才读取文件（路径不存在、非法 UTF-8 等）

测试方法：用同一条包含多处错误的输入逐步修正参数，每一步都要求
退出码为 2、标准输出严格为空、标准错误恰好是当前优先级最高的一条
参数错误加末尾换行，不出现后续参数错误、文件读取失败或无效日志警告。
空值（空字符串、三个空格）作为真实参数逐个传入，所有选项均有值。
另以同一组错误的不同选项排列复核优先级与选项位置无关。

补充边界：
- 起点与终点相等（合法空区间）不得提前结束校验流程，应继续走到文件
  读取阶段，因路径不存在返回退出码 2，标准错误包含“文件读取失败”与
  该路径（系统异常细节不作固定要求）。
- 用含非法 UTF-8 字节的临时文件复核输出开关互斥这一最高优先级：
  即使文件不可读，结果仍只有互斥错误，以此区分参数拒绝与文件读取错误。

测试数据与不存在的路径均由用例在临时目录中准备并自动清理，不依赖
sample.jsonl 等仓库样例、网络或外部服务。比较按原始字节进行
（不开 text=True、不做通用换行归一化），记录终止换行沿用运行平台
现有行为（os.linesep）。产品源码、公开命令参数与筛选语义均不改动。

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

# print 写标准错误时的平台记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 各校验环节的标准错误正文（不含末尾换行），与源码中的文案逐字对应。
MUTEX_ERROR = "参数错误：--jsonl 与 --summary 不能同时使用"
LEVEL_ERROR = (
    "参数错误：--level 必须是以下级别之一："
    "DEBUG, INFO, WARNING, ERROR, CRITICAL"
)
REQUEST_ID_ERROR = "参数错误：--request-id 不能为空或全为空白"
MESSAGE_CONTAINS_ERROR = "参数错误：--message-contains 不能为空或全为空白"
SINCE_ERROR = "参数错误：--since 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间"
UNTIL_ERROR = "参数错误：--until 必须是 YYYY-MM-DDTHH:MM:SSZ 格式的 UTC 时间"
RANGE_ERROR = "参数错误：--since 不能晚于 --until"

# 不得出现在参数校验失败输出中的后续阶段痕迹。
READ_FAILURE_MESSAGE = "文件读取失败".encode("utf-8")
INVALID_LOG_WARNING = "无效日志".encode("utf-8")
TRACEBACK = b"Traceback"


def run_cli_bytes(args):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。

    args 为完整参数列表（含路径）；空字符串等空值作为真实参数传入。
    不使用 text=True：避免通用换行翻译与严格解码在比较前改动子进程输出。
    """
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定；
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


def build_args(path, *, jsonl=True, level="TRACE", request_id="",
               message_contains="   ", since="bad", until="bad"):
    """构造命令参数：默认每个选项都带一处错误，调用方逐步修正。

    空值（空字符串、三个空格）作为真实参数值传入，所有选项均有值。
    """
    args = [
        str(path),
        "--level", level,
        "--request-id", request_id,
        "--message-contains", message_contains,
        "--since", since,
        "--until", until,
        "--summary",
    ]
    if jsonl:
        args.append("--jsonl")
    return args


# 逐步修正序列：每一步只修正一处错误，期望报告的错误随之按优先级后移。
# 时间取值使最后一步构成“起点晚于终点一秒”的区间错误。
PROGRESSIVE_STEPS = (
    ("全部错误并存，只报互斥", {}, MUTEX_ERROR),
    ("移除 --jsonl 后只报级别", {"jsonl": False}, LEVEL_ERROR),
    ("级别改为 ERROR 后只报请求标识", {"jsonl": False, "level": "ERROR"},
     REQUEST_ID_ERROR),
    ("标识改为 r-1 后只报消息候选",
     {"jsonl": False, "level": "ERROR", "request_id": "r-1"},
     MESSAGE_CONTAINS_ERROR),
    ("候选改为 timeout 后只报 --since 格式",
     {"jsonl": False, "level": "ERROR", "request_id": "r-1",
      "message_contains": "timeout"},
     SINCE_ERROR),
    ("起点改为合法值后只报 --until 格式",
     {"jsonl": False, "level": "ERROR", "request_id": "r-1",
      "message_contains": "timeout", "since": "2026-10-03T10:00:01Z"},
     UNTIL_ERROR),
    ("终点改为早于起点后只报区间顺序",
     {"jsonl": False, "level": "ERROR", "request_id": "r-1",
      "message_contains": "timeout", "since": "2026-10-03T10:00:01Z",
      "until": "2026-10-03T10:00:00Z"},
     RANGE_ERROR),
)


class ValidationPriorityTestCase(unittest.TestCase):
    """公共基类：提供临时目录、确定不存在的路径与逐字节断言。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 确定不存在的文件路径：位于空临时目录中，全程不创建。
        self.missing_path = Path(self._tmp.name) / "missing.jsonl"

    def assert_arg_error(self, proc, expected_message):
        """断言一次参数校验失败：退出码 2、标准输出为空、标准错误恰好一行。"""
        expected_stderr = (
            expected_message.encode("utf-8") + RECORD_TERMINATOR
        )
        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stdout, b"",
            f"参数错误时标准输出必须为空，实际 {proc.stdout!r}",
        )
        # 严格相等：只有当前这一条参数错误和末尾换行，
        # 不出现后一条参数错误、文件读取失败或无效日志警告。
        self.assertEqual(
            proc.stderr, expected_stderr,
            f"标准错误应只有一条参数错误：{expected_stderr!r}，"
            f"实际 {proc.stderr!r}",
        )
        self.assertNotIn(READ_FAILURE_MESSAGE, proc.stderr)
        self.assertNotIn(INVALID_LOG_WARNING, proc.stderr)
        self.assertNotIn(TRACEBACK, proc.stderr)


class ProgressiveFixTests(ValidationPriorityTestCase):
    """同一条多重错误输入逐步修正：每步只报告当前优先级最高的错误。"""

    def test_each_fix_reveals_exactly_the_next_error(self):
        for label, overrides, expected in PROGRESSIVE_STEPS:
            with self.subTest(步骤=label):
                args = build_args(self.missing_path, **overrides)
                proc = run_cli_bytes(args)
                self.assert_arg_error(proc, expected)

    def test_missing_file_is_never_read_before_validation_finishes(self):
        # 逐步序列中路径始终不存在，但任何一步都不得报告文件读取失败：
        # 单独固定“参数校验全部在读取文件之前完成”这一约定。
        for label, overrides, _ in PROGRESSIVE_STEPS:
            with self.subTest(步骤=label):
                args = build_args(self.missing_path, **overrides)
                proc = run_cli_bytes(args)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn(READ_FAILURE_MESSAGE, proc.stderr)


class OptionOrderIndependenceTests(ValidationPriorityTestCase):
    """调整不同选项在命令中的位置，校验优先级保持不变。"""

    def test_mutex_error_first_regardless_of_option_positions(self):
        # 同一组全部错误的参数，三种排列：路径在中间、开关分散、选项逆序。
        path = str(self.missing_path)
        arrangements = (
            ["--jsonl", path, "--summary", "--level", "TRACE",
             "--request-id", "", "--message-contains", "   ",
             "--since", "bad", "--until", "bad"],
            ["--level", "TRACE", "--since", "bad", path,
             "--request-id", "", "--jsonl", "--until", "bad",
             "--message-contains", "   ", "--summary"],
            ["--until", "bad", "--since", "bad", "--message-contains", "   ",
             "--request-id", "", "--level", "TRACE", "--summary",
             "--jsonl", path],
        )
        for index, args in enumerate(arrangements):
            with self.subTest(排列=index):
                proc = run_cli_bytes(args)
                self.assert_arg_error(proc, MUTEX_ERROR)

    def test_later_priority_error_first_regardless_of_option_positions(self):
        # 修正到“--since 格式错误”这一步的参数，调换选项位置后仍只报该错误。
        path = str(self.missing_path)
        arrangements = (
            [path, "--level", "ERROR", "--request-id", "r-1",
             "--message-contains", "timeout", "--since", "bad",
             "--until", "bad", "--summary"],
            ["--summary", "--until", "bad", "--since", "bad",
             "--message-contains", "timeout", "--request-id", "r-1",
             "--level", "ERROR", path],
            ["--since", "bad", path, "--level", "ERROR", "--until", "bad",
             "--summary", "--message-contains", "timeout",
             "--request-id", "r-1"],
        )
        for index, args in enumerate(arrangements):
            with self.subTest(排列=index):
                proc = run_cli_bytes(args)
                self.assert_arg_error(proc, SINCE_ERROR)


class EqualEndpointsEmptyIntervalTests(ValidationPriorityTestCase):
    """起点与终点相等是合法空区间：不提前结束校验，继续走到文件读取。"""

    def test_equal_since_until_proceeds_to_file_read_failure(self):
        args = build_args(
            self.missing_path,
            jsonl=False,
            level="ERROR",
            request_id="r-1",
            message_contains="timeout",
            since="2026-10-03T10:00:00Z",
            until="2026-10-03T10:00:00Z",
        )
        proc = run_cli_bytes(args)
        self.assertEqual(
            proc.returncode, 2,
            f"应退出 2，实际 {proc.returncode}；stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stdout, b"",
            f"读取失败时标准输出必须为空，实际 {proc.stdout!r}",
        )
        # 合法空区间不得触发区间顺序错误；应因路径不存在报文件读取失败，
        # 错误中带上该路径以便定位，系统异常细节不作固定要求。
        self.assertNotIn(RANGE_ERROR.encode("utf-8"), proc.stderr)
        self.assertIn(READ_FAILURE_MESSAGE, proc.stderr)
        self.assertIn(os.fsencode(str(self.missing_path)), proc.stderr)
        self.assertNotIn(INVALID_LOG_WARNING, proc.stderr)
        self.assertNotIn(TRACEBACK, proc.stderr)


class MutexBeatsUnreadableFileTests(ValidationPriorityTestCase):
    """互斥是最高优先级：即使文件含非法 UTF-8 字节，也只报互斥错误。"""

    def test_mutex_error_reported_before_utf8_read_failure(self):
        # 临时文件内容为非法 UTF-8 字节，读取必然失败；
        # 但互斥检查在读取文件之前，结果仍只有互斥错误。
        corrupt_path = Path(self._tmp.name) / "corrupt.jsonl"
        corrupt_path.write_bytes(b"\xff\xfe\x00invalid")
        args = build_args(
            corrupt_path,
            jsonl=True,
            level="ERROR",
            request_id="r-1",
            message_contains="timeout",
            since="2026-10-03T10:00:00Z",
            until="2026-10-03T10:00:01Z",
        )
        proc = run_cli_bytes(args)
        self.assert_arg_error(proc, MUTEX_ERROR)


if __name__ == "__main__":
    unittest.main()
