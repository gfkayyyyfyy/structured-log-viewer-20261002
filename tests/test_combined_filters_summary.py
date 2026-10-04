"""多级别 + 多请求标识 + 含起点不含终点时间区间组合筛选的 --summary 回归测试。

只用 Python 3 标准库（unittest / subprocess / json / tempfile / pathlib），
测试输入均在用例自建的临时目录中以 UTF-8、LF 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令参数、
模块接口与冻结文档均保持不变，仅通过命令行入口验证这一条组合筛选
``--summary`` 路径的退出码与两个输出流。

七行样例（末行带换行，对象字段均在顶层）：
- 第 1 行：ERROR、r-2、10:00:00，命中（含起点）；
- 第 2 行：只有空格，按空白行静默跳过；
- 第 3 行：not-json，产出 JSON 解析失败警告；
- 第 4 行：level 原文为 " warning "（规范化后 WARNING）、r-1、10:00:01，命中；
- 第 5 行：ERROR、r-12、10:00:01，请求标识不在所选集合，静默不匹配；
- 第 6 行：只有 INFO 与 request_id=other 两个字段，缺 timestamp，
  时间筛选启用后仍产出 timestamp 警告（尽管级别与标识都不命中）；
- 第 7 行：与第 1 行相同但 request_id 为 r-1、时间为 10:00:02，
  不严格早于 --until 终点（不含终点），静默不匹配。

执行：

    python -m log_viewer case.jsonl --level ERROR --level WARNING \\
        --request-id r-1 --request-id r-2 \\
        --since 2026-10-03T10:00:00Z --until 2026-10-03T10:00:02Z --summary

预期：退出码 0；标准输出只有一个 JSON 统计对象和末尾换行，
matched_count=2、invalid_count=2，by_level 中 ERROR、WARNING 各为 1，
其余支持级别为 0，不含日志原文、行号或制表符；标准错误依次只有
“第 3 行：无效日志：JSON 解析失败”和
“第 6 行：无效日志：timestamp 缺失或格式无效”两条警告。
交换 --request-id 顺序或重复添加 r-1，所有结果不变；--until 改为
与 --since 相等（合法空区间）后命中与各级别计数全为 0、invalid_count
仍为 2、警告与退出码不变。同一组筛选下默认逐行输出仍只给第 1、4 行。

从项目根目录执行：

    python -m unittest discover -s tests

成功时退出码为 0；有断言失败时退出码非零并列出失败用例。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 七行样例的物理正文（不含行分隔符），UTF-8、LF，末行带换行。
# ---------------------------------------------------------------------------
# 第 1 行：ERROR、r-2、恰在 --since 起点（含起点），应命中。
LINE1 = (
    '{"level":"ERROR","request_id":"r-2",'
    '"timestamp":"2026-10-03T10:00:00Z"}'
)
# 第 2 行：只有空格，空白行静默跳过，不产生警告。
LINE2 = "   "
# 第 3 行：不是 JSON，产出解析失败警告。
LINE3 = "not-json"
# 第 4 行：level 原文带首尾空格的小写 warning（规范化后命中 WARNING），
# r-1、时间在区间内，应命中。
LINE4 = (
    '{"level":" warning ","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:01Z"}'
)
# 第 5 行：ERROR、但 request_id 为 r-12，精确相等不通过，静默不匹配。
LINE5 = (
    '{"level":"ERROR","request_id":"r-12",'
    '"timestamp":"2026-10-03T10:00:01Z"}'
)
# 第 6 行：只有 level=INFO 与 request_id=other 两个顶层字段，缺 timestamp。
LINE6 = '{"level":"INFO","request_id":"other"}'
# 第 7 行：ERROR、r-1，但时间恰为 --until 终点（不含终点），静默不匹配。
LINE7 = (
    '{"level":"ERROR","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:02Z"}'
)
SEVEN_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7]
PAYLOAD = ("\n".join(SEVEN_LINES) + "\n").encode("utf-8")

# 标准错误：依次只有第 3 行 JSON 解析失败、第 6 行 timestamp 缺失两条警告。
EXPECTED_STDERR = (
    "第 3 行：无效日志：JSON 解析失败\n"
    "第 6 行：无效日志：timestamp 缺失或格式无效\n"
)

EXPECTED_BY_LEVEL = {
    "DEBUG": 0,
    "INFO": 0,
    "WARNING": 1,
    "ERROR": 1,
    "CRITICAL": 0,
}

# 组合筛选的公共选项：两个级别、两个请求标识与含起点不含终点的一秒区间。
FILTER_ARGS = (
    "--level", "ERROR",
    "--level", "WARNING",
    "--request-id", "r-1",
    "--request-id", "r-2",
    "--since", "2026-10-03T10:00:00Z",
    "--until", "2026-10-03T10:00:02Z",
)


def run_cli(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按 UTF-8 文本捕获输出。"""
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
        text=True,
        encoding="utf-8",
        env=env,
    )


class CombinedFiltersSummaryTests(unittest.TestCase):
    """多级别 + 多请求标识 + 时间区间组合筛选下的 --summary 摘要。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(PAYLOAD)
        self._path = str(path)

    def _run_summary(self, *request_id_args, until="2026-10-03T10:00:02Z"):
        return run_cli(
            self._path,
            "--level", "ERROR",
            "--level", "WARNING",
            *request_id_args,
            "--since", "2026-10-03T10:00:00Z",
            "--until", until,
            "--summary",
        )

    def test_fixture_layout_matches_spec(self):
        # 数据自检：七行、LF 终止且仅末行带换行、无 CR，对象字段均在顶层。
        self.assertEqual(len(SEVEN_LINES), 7)
        self.assertEqual(PAYLOAD.count(b"\n"), 7)
        self.assertFalse(PAYLOAD.endswith(b"\n\n"))
        self.assertNotIn(b"\r", PAYLOAD)
        self.assertIn('"level":"ERROR"', LINE1)
        self.assertIn('"request_id":"r-2"', LINE1)
        self.assertIn('"timestamp":"2026-10-03T10:00:00Z"', LINE1)
        self.assertTrue(LINE2.strip() == "" and LINE2 != "")
        self.assertEqual(LINE3, "not-json")
        self.assertIn('"level":" warning "', LINE4)
        self.assertIn('"request_id":"r-1"', LINE4)
        self.assertIn('"timestamp":"2026-10-03T10:00:01Z"', LINE4)
        self.assertIn('"request_id":"r-12"', LINE5)
        # 第 6 行恰好只有 level、request_id 两个顶层字段。
        self.assertEqual(
            set(json.loads(LINE6)), {"level", "request_id"}
        )
        self.assertEqual(json.loads(LINE6)["request_id"], "other")
        self.assertIn('"request_id":"r-1"', LINE7)
        self.assertIn('"timestamp":"2026-10-03T10:00:02Z"', LINE7)

    def test_summary_counts_and_by_level(self):
        proc = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        # 摘要恰有三个约定字段，无额外键。
        self.assertEqual(
            set(obj), {"matched_count", "invalid_count", "by_level"}
        )
        self.assertEqual(obj["matched_count"], 2)
        self.assertEqual(obj["invalid_count"], 2)
        # ERROR、WARNING 各 1，其余支持级别均为 0。
        self.assertEqual(obj["by_level"], EXPECTED_BY_LEVEL)
        self.assertEqual(sum(obj["by_level"].values()), 2)

    def test_stdout_is_one_json_object_with_single_trailing_newline(self):
        proc = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = {
            "matched_count": 2,
            "invalid_count": 2,
            "by_level": EXPECTED_BY_LEVEL,
        }
        # 标准输出逐字符等于一个 JSON 对象加恰好一个末尾换行：
        # 不多余空行，无外层数组。
        self.assertEqual(
            proc.stdout, json.dumps(summary, ensure_ascii=False) + "\n"
        )
        self.assertTrue(proc.stdout.endswith("\n"))
        self.assertFalse(proc.stdout.endswith("\n\n"))
        # 不输出日志原文或行号：不含制表符前缀、命中行正文或 not-json。
        self.assertNotIn("\t", proc.stdout)
        self.assertNotIn(LINE1, proc.stdout)
        self.assertNotIn(LINE4, proc.stdout)
        self.assertNotIn("not-json", proc.stdout)
        self.assertFalse(proc.stdout.lstrip().startswith("1"))

    def test_stderr_has_exactly_two_warnings_in_order(self):
        proc = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准错误依次只有两条既有文案警告，各带一个换行，无其他输出。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)
        self.assertEqual(proc.stderr.count("\n"), 2)
        # JSON 解析失败（第 3 行）在前，timestamp 缺失（第 6 行）在后。
        self.assertLess(
            proc.stderr.find("第 3 行：无效日志：JSON 解析失败"),
            proc.stderr.find("第 6 行：无效日志：timestamp 缺失或格式无效"),
        )

    def test_swapping_request_id_order_does_not_change_results(self):
        baseline = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2"
        )
        swapped = self._run_summary(
            "--request-id", "r-2", "--request-id", "r-1"
        )
        for proc in (baseline, swapped):
            self.assertEqual(proc.returncode, 0, proc.stderr)
        # 交换请求标识顺序：退出码、两个输出流全部一致。
        self.assertEqual(swapped.returncode, baseline.returncode)
        self.assertEqual(swapped.stdout, baseline.stdout)
        self.assertEqual(swapped.stderr, baseline.stderr)

    def test_repeating_request_id_does_not_change_results(self):
        baseline = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2"
        )
        repeated = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2",
            "--request-id", "r-1",
        )
        for proc in (baseline, repeated):
            self.assertEqual(proc.returncode, 0, proc.stderr)
        # 再添加一次 r-1：重复标识不多计数，所有结果不变。
        self.assertEqual(repeated.stdout, baseline.stdout)
        self.assertEqual(repeated.stderr, baseline.stderr)
        obj = json.loads(repeated.stdout)
        self.assertEqual(obj["matched_count"], 2)
        self.assertEqual(obj["invalid_count"], 2)

    def test_empty_interval_zeroes_matches_but_keeps_invalids(self):
        # 仅把 --until 改为与 --since 相等：合法空区间，其余参数不变。
        proc = self._run_summary(
            "--request-id", "r-1", "--request-id", "r-2",
            until="2026-10-03T10:00:00Z",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        obj = json.loads(proc.stdout)
        # 命中与各级别计数全为 0；invalid_count 仍为 2。
        self.assertEqual(obj["matched_count"], 0)
        self.assertEqual(obj["invalid_count"], 2)
        self.assertEqual(
            obj["by_level"],
            {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0},
        )
        # 标准输出仍是一个 JSON 对象加末尾换行。
        self.assertEqual(
            proc.stdout,
            json.dumps(
                {"matched_count": 0, "invalid_count": 2,
                 "by_level": obj["by_level"]},
                ensure_ascii=False,
            ) + "\n",
        )
        # 警告及退出码不变。
        self.assertEqual(proc.stderr, EXPECTED_STDERR)

    def test_default_line_output_unchanged_under_same_filters(self):
        # 不加 --summary：默认逐行输出保持原状，仍只给第 1、4 行，
        # 格式为“行号<Tab>原始正文”，按文件行序；警告仍走标准错误。
        proc = run_cli(self._path, *FILTER_ARGS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "1\t" + LINE1 + "\n" + "4\t" + LINE4 + "\n",
        )
        # 不匹配的第 5、7 行与空白/无效行不出现在标准输出。
        self.assertNotIn(LINE5, proc.stdout)
        self.assertNotIn(LINE7, proc.stdout)
        self.assertEqual(proc.stderr, EXPECTED_STDERR)


if __name__ == "__main__":
    unittest.main()
