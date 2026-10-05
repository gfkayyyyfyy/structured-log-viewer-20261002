"""--strict 无值开关的离线回归测试。

需求约定（见 README）：不传 --strict 时保持现状，文件正常处理完成即
退出 0，包括存在无效行的情况；传入后仍检查全文件、完成筛选和输出，
只要本次产出至少一条无效日志警告，最终退出码即为 1，否则为 0。
遇到无效行不能提前结束，也不能丢弃后续有效结果。重复传入等同于一次；
--strict=true 等带值写法属于 argparse 参数错误：退出码 2、标准输出为空。

严格模式沿用当前无效行判定（损坏 JSON、顶层非对象、level 缺失或非法；
启用 --since/--until 时合法级别记录的 timestamp 缺失或非法），每行最多
一条警告；级别、请求标识、消息、时间条件未匹配以及空白行都不算错误。
--line-range 只限制匹配结果，范围外无效行仍使退出码为 1；即使没有匹配
或起止时间相等，仍按全文件警告决定状态。三种输出模式（默认逐行、
--summary、--jsonl）的两路输出与未开启时逐字节一致，不追加严格模式提示。

测试输入全部按字节在用例自建的临时目录中构造并自动清理，不依赖
strict-demo.jsonl、sample.jsonl 等外部样例、网络或外部服务。

比较按原始字节进行（不开 text=True、不做通用换行归一化）；
退出码、标准输出、标准错误分别独立断言。

只用 Python 标准库。从项目根目录执行：

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

# print 写标准输出/标准错误时的平台记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# 验收夹具 strict-demo.jsonl 的三个物理行（均以 LF 结束）：
# 第 1 行匹配 ERROR；第 2 行损坏 JSON；第 3 行级别不匹配（不算无效）。
LINE1 = '{"level":"ERROR"}'
LINE2 = "not-json"
LINE3 = '{"level":"INFO"}'

DEMO_BYTES = (
    LINE1.encode("utf-8") + b"\n"
    + LINE2.encode("utf-8") + b"\n"
    + LINE3.encode("utf-8") + b"\n"
)

DEMO_STDERR = (
    "第 2 行：无效日志：JSON 解析失败".encode("utf-8") + RECORD_TERMINATOR
)

DEMO_SUMMARY = {
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


def run_cli_bytes(path, *extra, level="ERROR"):
    """运行 ``python -m log_viewer``，按原始字节捕获两个输出流。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", str(path), "--level", level,
         *extra],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class FixtureSelfCheckTests(unittest.TestCase):
    """测试夹具自检：三行均以 LF 结束。"""

    def test_demo_fixture_shape(self):
        self.assertEqual(DEMO_BYTES.count(b"\n"), 3)
        self.assertTrue(DEMO_BYTES.endswith(b"\n"))
        self.assertEqual(
            DEMO_BYTES.decode("utf-8").split("\n"),
            [LINE1, LINE2, LINE3, ""],
        )


class StrictAcceptanceTests(unittest.TestCase):
    """strict-demo 验收：三种输出模式下开关只改退出码，输出逐字节不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "strict-demo.jsonl"
        self.path.write_bytes(DEMO_BYTES)

    def test_default_mode_without_strict_exits_zero_despite_invalid_line(self):
        proc = run_cli_bytes(self.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 只有第 1 行的行号、制表符与原文。
        self.assertEqual(
            proc.stdout,
            b"1\t" + LINE1.encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(proc.stderr, DEMO_STDERR)

    def test_default_mode_with_strict_exits_one_with_identical_output(self):
        plain = run_cli_bytes(self.path)
        strict = run_cli_bytes(self.path, "--strict")
        self.assertEqual(strict.returncode, 1, strict.stderr)
        # 两路输出与未开启时完全一致，不追加严格模式提示。
        self.assertEqual(strict.stdout, plain.stdout)
        self.assertEqual(strict.stderr, plain.stderr)
        self.assertEqual(
            strict.stdout,
            b"1\t" + LINE1.encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(strict.stderr, DEMO_STDERR)

    def test_summary_mode_counts_unchanged_and_strict_sets_exit_one(self):
        plain = run_cli_bytes(self.path, "--summary")
        strict = run_cli_bytes(self.path, "--summary", "--strict")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(strict.returncode, 1, strict.stderr)
        self.assertEqual(strict.stdout, plain.stdout)
        self.assertEqual(strict.stderr, plain.stderr)
        summary = json.loads(
            strict.stdout[: -len(RECORD_TERMINATOR)].decode("utf-8")
        )
        self.assertEqual(summary, DEMO_SUMMARY)

    def test_jsonl_mode_output_unchanged_and_strict_sets_exit_one(self):
        plain = run_cli_bytes(self.path, "--jsonl")
        strict = run_cli_bytes(self.path, "--jsonl", "--strict")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(strict.returncode, 1, strict.stderr)
        # 只有第一行正文和末尾 LF。
        self.assertEqual(strict.stdout, LINE1.encode("utf-8") + b"\n")
        self.assertEqual(strict.stdout, plain.stdout)
        self.assertEqual(strict.stderr, DEMO_STDERR)

    def test_invalid_line_does_not_abort_later_valid_lines(self):
        # 无效行（第 2 行）之后的有效记录仍照常输出；第 3 行 INFO 合法，
        # 在 --level INFO 下必须出现在结果中，证明没有提前结束或丢弃。
        proc = run_cli_bytes(self.path, "--strict", level="INFO")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            proc.stdout,
            b"3\t" + LINE3.encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(proc.stderr, DEMO_STDERR)


class StrictFlagSyntaxTests(unittest.TestCase):
    """开关语法：重复等同于一次，带值写法为参数错误。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "strict-demo.jsonl"
        self.path.write_bytes(DEMO_BYTES)

    def test_repeated_strict_equals_single(self):
        once = run_cli_bytes(self.path, "--strict")
        twice = run_cli_bytes(self.path, "--strict", "--strict")
        self.assertEqual(twice.returncode, 1, twice.stderr)
        self.assertEqual(twice.stdout, once.stdout)
        self.assertEqual(twice.stderr, once.stderr)

    def test_strict_with_value_is_argument_error_exit_two_empty_stdout(self):
        for flag in ("--strict=true", "--strict=false", "--strict=1"):
            with self.subTest(flag=flag):
                proc = run_cli_bytes(self.path, flag)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(
                    proc.stdout, b"",
                    f"参数错误时标准输出必须为空：{proc.stdout!r}",
                )
                # argparse 的用法/错误文案，非应用自身的警告，也不读文件产出。
                self.assertNotIn(b"\xe6\x97\xa0\xe6\x95\x88\xe6\x97\xa5\xe5\xbf\x97",
                                 proc.stderr)  # “无效日志”
                self.assertIn(b"--strict", proc.stderr)


class StrictCleanFileTests(unittest.TestCase):
    """无无效行时 --strict 退出 0：未匹配与空白行都不算错误。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, payload):
        path = Path(self._tmp.name) / name
        path.write_bytes(payload)
        return path

    def test_clean_file_exits_zero_under_strict(self):
        path = self._write(
            "clean.jsonl",
            b'{"level":"INFO"}\n{"level":"ERROR"}\n',
        )
        proc = run_cli_bytes(path, "--strict")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")

    def test_level_mismatch_is_not_an_error_under_strict(self):
        # 合法记录只是未匹配级别：不算无效，退出 0，无输出无警告。
        path = self._write("mismatch.jsonl", b'{"level":"WARNING"}\n')
        proc = run_cli_bytes(path, "--strict")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def test_blank_lines_are_not_errors_under_strict(self):
        path = self._write(
            "blanks.jsonl",
            b"\n   \n\t\n" + b'{"level":"INFO"}\n',
        )
        proc = run_cli_bytes(path, "--strict", level="INFO")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 物理行号不重排：INFO 在第 4 行。
        self.assertEqual(
            proc.stdout,
            b"4\t" + '{"level":"INFO"}'.encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(proc.stderr, b"")

    def test_request_id_message_mismatch_is_not_an_error_under_strict(self):
        path = self._write(
            "fields.jsonl",
            b'{"level":"ERROR","request_id":"a","message":"x"}\n',
        )
        proc = run_cli_bytes(
            path, "--strict",
            "--request-id", "b",
            "--message-contains", "y",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def test_strict_alone_does_not_enable_timestamp_checks(self):
        # 不给 --since/--until 时，即使合法级别记录缺少 timestamp，
        # 单独开启 --strict 也不产生警告、退出 0（时间条件未匹配同理）。
        path = self._write(
            "notime.jsonl",
            b'{"level":"ERROR"}\n{"level":"INFO"}\n',
        )
        proc = run_cli_bytes(path, "--strict")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")

    def test_strict_with_time_window_flags_missing_timestamp(self):
        # 对照：一旦给出 --since，timestamp 缺失就按既有规则成为无效行。
        path = self._write("notime.jsonl", b'{"level":"ERROR"}\n')
        proc = run_cli_bytes(
            path, "--strict", "--since", "2020-01-01T00:00:00Z"
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
            + RECORD_TERMINATOR,
        )


class StrictInvalidClassificationTests(unittest.TestCase):
    """三类既有无效行判定在严格模式下各产生至多一条警告并使退出码为 1。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, payload):
        path = Path(self._tmp.name) / "x.jsonl"
        path.write_bytes(payload)
        return path

    def test_broken_json_non_object_and_bad_level_each_warn_once(self):
        cases = [
            (b"not-json\n", "第 1 行：无效日志：JSON 解析失败"),
            (b'["array"]\n', "第 1 行：无效日志：顶层不是 JSON 对象"),
            (b'{"level":"WAT"}\n',
             "第 1 行：无效日志：level 缺失或不属于支持的级别"),
            (b'{"foo":1}\n',
             "第 1 行：无效日志：level 缺失或不属于支持的级别"),
        ]
        for payload, warning in cases:
            with self.subTest(payload=payload):
                path = self._write(payload)
                proc = run_cli_bytes(path, "--strict")
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertEqual(
                    proc.stderr,
                    warning.encode("utf-8") + RECORD_TERMINATOR,
                )
                # 每行最多一条警告。
                self.assertEqual(proc.stderr.count(b"\xe7\xac\xac"), 1)

    def test_each_invalid_line_warns_at_most_once(self):
        # 损坏 JSON 不会再因顶层类型或 level 继续产出第二、三条警告。
        path = self._write(b"null\n")
        proc = run_cli_bytes(path, "--strict")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            proc.stderr,
            "第 1 行：无效日志：顶层不是 JSON 对象".encode("utf-8")
            + RECORD_TERMINATOR,
        )


class StrictLineRangeTests(unittest.TestCase):
    """--line-range 只限制匹配结果；全文件警告仍决定严格模式退出码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_invalid_line_outside_range_still_sets_exit_one(self):
        path = Path(self._tmp.name) / "x.jsonl"
        path.write_bytes(
            b'{"level":"ERROR"}\nnot-json\n{"level":"ERROR"}\n'
        )
        # 只输出第 1 行；第 2 行的无效警告在范围外照常产出。
        proc = run_cli_bytes(path, "--strict", "--line-range", "1:1")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            proc.stdout,
            b"1\t" + '{"level":"ERROR"}'.encode("utf-8") + RECORD_TERMINATOR,
        )
        self.assertEqual(proc.stderr, DEMO_STDERR)

    def test_no_match_but_invalid_line_still_sets_exit_one(self):
        path = Path(self._tmp.name) / "x.jsonl"
        # 区间只覆盖无效行，两个匹配行都在范围外：没有匹配，仍退出 1。
        path.write_bytes(
            b'{"level":"ERROR"}\nnot-json\n{"level":"ERROR"}\n'
        )
        proc = run_cli_bytes(path, "--strict", "--line-range", "2:2")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, DEMO_STDERR)

    def test_empty_window_equal_endpoints_status_follows_warnings(self):
        # 起止时间相等是合法空区间：记录合法但落在区间外时退出 0；
        # 记录缺 timestamp 而被警告时退出 1。
        path = Path(self._tmp.name) / "x.jsonl"
        path.write_bytes(
            b'{"level":"ERROR","timestamp":"2020-01-02T00:00:00Z"}\n'
        )
        proc = run_cli_bytes(
            path, "--strict",
            "--since", "2020-01-01T00:00:00Z",
            "--until", "2020-01-01T00:00:00Z",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")


if __name__ == "__main__":
    unittest.main()
