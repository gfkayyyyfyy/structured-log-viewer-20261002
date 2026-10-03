"""多级别 + 请求标识 + 含起点不含终点时间区间组合筛选的 --jsonl 导出回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / os），
测试输入均在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、公开命令参数
与模块接口均保持不变，仅验证组合筛选下的 --jsonl 导出这一条路径。

覆盖要点：
- 八行样例在 ``--level ERROR --level WARNING --level ERROR``
  ``--request-id r-1 --since 2026-10-03T10:00:00Z --until 2026-10-03T10:00:02Z``
  ``--jsonl`` 下：标准输出按 UTF-8 字节仅等于第 2、8 行原始正文各加一个
  LF——不含行号、不重新序列化、重复选择 ERROR 不多输出；早于起点的第 1 行、
  请求标识不符的第 3 行、不早于终点的第 5 行均被排除。
- 标准错误依次只有第 4 行的“timestamp 缺失或格式无效”警告与第 7 行的
  “JSON 解析失败”警告，保留既有文案与物理行号，换行沿用当前平台规则；
  第 4 行级别（INFO）不在所选集合内，仍因时间筛选启用而产生警告。
- 其余输入与参数不变、仅把 --until 改为与 --since 相等（合法空区间）时：
  标准输出为空、标准错误仍是同样两条警告、退出码 0。
- 两种结果的导出正文、警告内容与退出码分别独立断言，失败时可区分差异来源。

比较输出时按原始字节捕获子进程的标准输出/标准错误（不开 text=True），
不做通用换行归一化。导出模式直接写字节流，记录终止符固定为 LF；
标准错误警告沿用 print 的平台终止换行（os.linesep），在 Linux 上为 LF。

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

# print 写标准错误警告时的平台记录终止换行，按字节比较。
RECORD_TERMINATOR = os.linesep.encode("utf-8")

# ---------------------------------------------------------------------------
# 八行样例的物理正文（不含行分隔符）。
# 除注明差异外，JSON 对象的 request_id 均为 "r-1"，
# timestamp 均为 "2026-10-03T10:00:00Z"。
# ---------------------------------------------------------------------------
# 第 1 行：ERROR，但时间早于 --since 起点（含起点，故被排除）。
LINE1 = (
    '{"level":"ERROR","request_id":"r-1",'
    '"timestamp":"2026-10-03T09:59:59Z","message":"过早"}'
)
# 第 2 行：WARNING，命中所选级别且其余条件全部满足，应导出；含中文 message。
LINE2 = (
    '{"level":"WARNING","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:00Z","message":"中文 警告"}'
)
# 第 3 行：ERROR，但 request_id 为 r-2，精确相等比较不通过。
LINE3 = (
    '{"level":"ERROR","request_id":"r-2",'
    '"timestamp":"2026-10-03T10:00:00Z","message":"他人请求"}'
)
# 第 4 行：INFO 且 timestamp 为 null。级别虽不在所选集合内，
# 但时间筛选启用后所有 level 合法的记录都检查 timestamp，仍须警告。
LINE4 = (
    '{"level":"INFO","request_id":"r-1",'
    '"timestamp":null,"message":"无时间"}'
)
# 第 5 行：ERROR，但时间不严格早于 --until 终点（不含终点，故被排除）。
LINE5 = (
    '{"level":"ERROR","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:02Z","message":"过晚"}'
)
# 第 6 行：仅含空格，按空白行跳过，不产生警告。
LINE6 = "   "
# 第 7 行：不是 JSON，产生解析失败警告。
LINE7 = "not-json"
# 第 8 行：level 原文为带首尾空格的小写 error（规范化后命中），
# 时间在区间内，应导出；整条正文外侧另有空格和制表符，
# 用于核对导出逐字节保留原文、不重新序列化；含中文 message。
LINE8 = (
    '  \t{"level":" error ","request_id":"r-1",'
    '"timestamp":"2026-10-03T10:00:01Z","message":"中文 故障"}\t  '
)
EIGHT_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7, LINE8]
PAYLOAD = ("\n".join(EIGHT_LINES) + "\n").encode("utf-8")

# 组合筛选 + --jsonl 的标准输出：仅第 2、8 行原始正文，各补一个 LF。
EXPORT_BYTES = (LINE2 + "\n" + LINE8 + "\n").encode("utf-8")

# 标准错误：第 4 行 timestamp 警告在前，第 7 行 JSON 解析失败警告在后，
# 各自以平台记录终止换行结束。
WARNING_BYTES = (
    "第 4 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
    + RECORD_TERMINATOR
    + "第 7 行：无效日志：JSON 解析失败".encode("utf-8")
    + RECORD_TERMINATOR
)

# 组合筛选的公共参数：重复选择 ERROR 不多输出，选项顺序不影响结果。
FILTER_ARGS = (
    "--level", "ERROR",
    "--level", "WARNING",
    "--level", "ERROR",
    "--request-id", "r-1",
    "--since", "2026-10-03T10:00:00Z",
)


def run_cli_bytes(*args):
    """在项目根目录下运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行，把标准输出中的 CR、CRLF
    都翻译成 LF，掩盖正文的字节差异。
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


class CombinedFiltersJsonlExportTests(unittest.TestCase):
    """多级别 + 请求标识 + 时间区间组合筛选下的 --jsonl 导出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "case.jsonl"
        path.write_bytes(PAYLOAD)
        self._path = str(path)

    def test_fixture_layout_matches_spec(self):
        # 数据自检：样例确为八个物理行，各行差异符合约定。
        self.assertEqual(len(EIGHT_LINES), 8)
        self.assertEqual(PAYLOAD.count(b"\n"), 8)
        self.assertIn('"timestamp":"2026-10-03T09:59:59Z"', LINE1)
        self.assertIn('"request_id":"r-2"', LINE3)
        self.assertIn('"timestamp":null', LINE4)
        self.assertIn('"timestamp":"2026-10-03T10:00:02Z"', LINE5)
        self.assertTrue(LINE6.strip() == "" and LINE6 != "")
        self.assertEqual(LINE7, "not-json")
        # 第 8 行 level 原文带首尾空格且小写；正文外侧有空格与制表符。
        self.assertIn('"level":" error "', LINE8)
        self.assertTrue(LINE8.startswith("  \t"))
        self.assertTrue(LINE8.endswith("\t  "))
        # 第 2、8 行都含中文 message。
        self.assertIn("中文", LINE2)
        self.assertIn("中文", LINE8)

    def test_combined_filters_export_two_raw_bodies(self):
        proc = run_cli_bytes(
            self._path,
            *FILTER_ARGS,
            "--until", "2026-10-03T10:00:02Z",
            "--jsonl",
        )
        # 退出码、导出正文、警告内容分别独立断言，失败时可区分差异来源。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出按 UTF-8 字节仅等于第 2、8 行原始正文各加一个 LF。
        self.assertEqual(proc.stdout, EXPORT_BYTES)
        # 标准错误依次只有第 4 行与第 7 行的两条既有文案警告。
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_combined_filters_export_body_shape(self):
        proc = run_cli_bytes(
            self._path,
            *FILTER_ARGS,
            "--until", "2026-10-03T10:00:02Z",
            "--jsonl",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 每条记录恰以一个 LF 结束（共两个），无 CRLF、无多余结尾。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r", proc.stdout)
        # 不含行号制表符前缀，不加外层数组或统计字段。
        self.assertFalse(proc.stdout.startswith(b"2\t"))
        self.assertNotIn(b"\t2", proc.stdout)
        self.assertFalse(proc.stdout.startswith(b"["))
        self.assertNotIn(b"matched_count", proc.stdout)
        # 第 8 行正文外侧的空格与制表符逐字节保留，未被重新序列化。
        self.assertIn(("\t  \n").encode("utf-8"), proc.stdout)
        self.assertTrue(proc.stdout.endswith(LINE8.encode("utf-8") + b"\n"))
        # 重复选择 ERROR 不多输出：第 2 行在前、第 8 行在后，各出现一次。
        self.assertEqual(proc.stdout.count(LINE2.encode("utf-8")), 1)
        self.assertEqual(proc.stdout.count(LINE8.encode("utf-8")), 1)
        self.assertLess(
            proc.stdout.find(LINE2.encode("utf-8")),
            proc.stdout.find(LINE8.encode("utf-8")),
        )
        # 被排除的第 1、3、5 行正文不出现在导出中。
        self.assertNotIn(LINE1.encode("utf-8"), proc.stdout)
        self.assertNotIn(LINE3.encode("utf-8"), proc.stdout)
        self.assertNotIn(LINE5.encode("utf-8"), proc.stdout)
        self.assertEqual(proc.stderr, WARNING_BYTES)

    def test_empty_interval_exports_nothing_but_keeps_warnings(self):
        # 仅把 --until 改为与 --since 相等：合法空区间，其余输入与参数不变。
        proc = run_cli_bytes(
            self._path,
            *FILTER_ARGS,
            "--until", "2026-10-03T10:00:00Z",
            "--jsonl",
        )
        # 退出码、导出正文、警告内容分别独立断言。
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 标准输出为空：没有记录严格早于起点。
        self.assertEqual(proc.stdout, b"")
        # 标准错误仍是同样两条警告（空区间仍检查无效记录）。
        self.assertEqual(proc.stderr, WARNING_BYTES)


if __name__ == "__main__":
    unittest.main()
