"""组合筛选（多级别 + request_id + 含起点不含终点的时间区间）下
--jsonl 导出路径的离线回归测试。

只用 Python 3 标准库（unittest / subprocess / tempfile / os / sys），
测试输入在用例自建的临时目录中以 UTF-8 生成并在结束时清理，
不依赖 sample.jsonl、cr.jsonl、网络或外部服务；产品代码、命令参数、
筛选语义与模块接口均保持不变，本文件只验证 --jsonl 这一条导出路径。

八行样例（除注明差异外，request_id 均为 r-1，timestamp 均为
2026-10-03T10:00:00Z）：
- 第 1 行 ERROR，时间 09:59:59：早于 --since，静默跳过；
- 第 2 行 WARNING（含中文 message）：级别、请求标识、时间均命中；
- 第 3 行 ERROR，request_id 为 r-2：请求标识不匹配，静默跳过；
- 第 4 行 INFO，timestamp 为 null：尽管级别不在所选集合内，启用时间
  筛选后仍须产出“timestamp 缺失或格式无效”警告并跳过；
- 第 5 行 ERROR，时间 10:00:02：不早于不含终点的 --until，静默跳过；
- 第 6 行仅含空白：跳过，无警告；
- 第 7 行 not-json：JSON 解析失败警告；
- 第 8 行 level 为带首尾空格的小写 " error "，时间 10:00:01，
  含中文 message，正文外侧另有空格和制表符：规范化后命中 ERROR，
  导出时正文（含外侧空白）逐字节保留。

核对命令与用户手动核对完全一致（含重复的 --level ERROR）：

    python -m log_viewer case.jsonl \
        --level ERROR --level WARNING --level ERROR \
        --request-id r-1 \
        --since 2026-10-03T10:00:00Z --until 2026-10-03T10:00:02Z \
        --jsonl

期望：
- 退出码 0；
- 标准输出按 UTF-8 字节仅等于第 2、8 行原始正文各加一个 LF，
  不含行号前缀、不重新序列化、不因重复级别多输出；
- 标准错误依次只有第 4 行的 timestamp 警告与第 7 行的 JSON 解析
  失败警告（既有文案与物理行号），换行沿用当前平台规则。

再把 --until 改成与 --since 相同（合法空区间），其余参数不变：
标准输出为空，标准错误仍是同样两条警告，退出码 0。

两种结果各自独立断言退出码、导出正文与警告内容，任一差异都能在
失败信息中区分出来。

比较输出时按原始字节捕获子进程输出（不开 text=True），不做通用换行
归一化：导出模式记录终止符固定为 LF；标准错误沿用 print 在当前
平台的终止换行（os.linesep）。

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

# 标准错误警告由 print 写出，终止换行沿用当前平台规则。
WARNING_TERMINATOR = os.linesep.encode("utf-8")

BOUNDARY = "2026-10-03T10:00:00Z"
ONE_SECOND_BEFORE = "2026-10-03T09:59:59Z"
ONE_SECOND_AFTER = "2026-10-03T10:00:01Z"
TWO_SECONDS_AFTER = "2026-10-03T10:00:02Z"
REQUEST_ID = "r-1"

# ---------------------------------------------------------------------------
# 八个物理行的原始正文（不含行分隔符）。字段顺序、空白与中文都按写定的
# 字面形式保留，导出时必须逐字节回显，不得重新序列化。
# ---------------------------------------------------------------------------
# 第 1 行：ERROR 但时间早于区间起点（含起点），静默跳过。
LINE1 = (
    '{"level":"ERROR","request_id":"r-1","timestamp":"'
    + ONE_SECOND_BEFORE
    + '","message":"早于起点"}'
)
# 第 2 行：WARNING 命中（所选级别含 WARNING），含中文 message，
# 时间恰好等于含起点的 --since，落在区间内。
LINE2 = (
    '{"level":"WARNING","request_id":"r-1","timestamp":"'
    + BOUNDARY
    + '","message":"第二行 警告 你好"}'
)
# 第 3 行：ERROR 但 request_id 是 r-2，请求标识不匹配，静默跳过。
LINE3 = (
    '{"level":"ERROR","request_id":"r-2","timestamp":"'
    + BOUNDARY
    + '","message":"其他请求"}'
)
# 第 4 行：INFO（级别不在所选集合内）且 timestamp 为 null；启用时间
# 筛选后该记录仍须接受时间检查，产出 timestamp 警告，而非静默跳过。
LINE4 = (
    '{"level":"INFO","request_id":"r-1","timestamp":null,'
    '"message":"第四行 时间为空"}'
)
# 第 5 行：ERROR，时间等于不含终点的 --until，静默跳过。
LINE5 = (
    '{"level":"ERROR","request_id":"r-1","timestamp":"'
    + TWO_SECONDS_AFTER
    + '","message":"抵达终点"}'
)
# 第 6 行：仅含空格（与制表符）的物理行，跳过且无警告。
LINE6 = "   \t  "
# 第 7 行：非法 JSON 文本。
LINE7 = "not-json"
# 第 8 行：level 原文为带首尾空格的小写 error，规范化后等于 ERROR；
# 整条正文外侧另有空格和制表符，含中文 message；时间在区间内部。
# 导出时这些外侧空白与正文写法必须逐字节保留。
LINE8 = (
    '  \t{"level":" error ","request_id":"r-1","timestamp":"'
    + ONE_SECOND_AFTER
    + '","message":"第八行 错误 失败"}\t  '
)
EIGHT_LINES = [LINE1, LINE2, LINE3, LINE4, LINE5, LINE6, LINE7, LINE8]

# 与用户核对命令一致的参数（注意 ERROR 重复出现，验证重复级别不多输出）。
LEVEL_ARGS = ("--level", "ERROR", "--level", "WARNING", "--level", "ERROR")
RANGE_ARGS = ("--since", BOUNDARY, "--until", TWO_SECONDS_AFTER)

# 半开区间场景的标准输出：仅第 2、8 行原始正文，各补一个 LF。
EXPECTED_EXPORT_BYTES = (LINE2 + "\n" + LINE8 + "\n").encode("utf-8")

# 两种场景共用的标准错误：依次为第 4 行 timestamp 警告、第 7 行 JSON
# 解析失败警告，文案与物理行号固定，终止换行随平台。
EXPECTED_WARNING_BYTES = (
    "第 4 行：无效日志：timestamp 缺失或格式无效".encode("utf-8")
    + WARNING_TERMINATOR
    + "第 7 行：无效日志：JSON 解析失败".encode("utf-8")
    + WARNING_TERMINATOR
)


def run_cli_bytes(path, *extra):
    """在项目根目录运行 ``python -m log_viewer``，按原始字节捕获输出。

    不使用 text=True：文本模式会启用通用换行翻译，掩盖正文与终止符
    的字节差异。
    """
    env = dict(os.environ)
    # 固定子进程的输出编码与标准库消息语言，使断言跨环境稳定；
    # 应用自身的中文消息不受这些变量影响。
    env["PYTHONIOENCODING"] = "utf-8"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [sys.executable, "-m", "log_viewer", path, *extra],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        env=env,
    )


class CombinedFiltersJsonlExportTests(unittest.TestCase):
    """多级别 + request_id + 半开时间区间同时使用时的 --jsonl 导出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "case.jsonl")
        Path(self.path).write_bytes(
            ("\n".join(EIGHT_LINES) + "\n").encode("utf-8")
        )

    def test_fixture_self_check_eight_physical_lines(self):
        # 数据自检：样例确为八个物理行，关键差异行的写法符合约定。
        with open(self.path, "rb") as f:
            physical_lines = f.read().split(b"\n")[:-1]
        self.assertEqual(len(physical_lines), 8)
        # 第 4 行 timestamp 为 null 且级别是 INFO（不匹配但仍须警告）。
        self.assertIn(b'"level":"INFO"', LINE4.encode("utf-8"))
        self.assertIn(b'"timestamp":null', LINE4.encode("utf-8"))
        # 第 6 行仅含空白；第 7 行是 not-json。
        self.assertEqual(LINE6.strip(), "")
        self.assertEqual(LINE7, "not-json")
        # 第 8 行 level 为带首尾空格的小写 error，正文外侧有空格和制表符。
        self.assertIn('"level":" error "', LINE8)
        self.assertTrue(LINE8.startswith("  \t"))
        self.assertTrue(LINE8.endswith("\t  "))
        # 第 2、8 行均含中文 message。
        self.assertIn("警告", LINE2)
        self.assertIn("错误", LINE8)

    def test_combined_filters_export_only_matching_raw_bodies(self):
        proc = run_cli_bytes(
            self.path,
            *LEVEL_ARGS,
            "--request-id", REQUEST_ID,
            *RANGE_ARGS,
            "--jsonl",
        )

        # 断言一：退出码为 0（独立于输出内容，先定位退出码差异）。
        self.assertEqual(
            proc.returncode, 0,
            "退出码非 0；stderr=" + proc.stderr.decode("utf-8", "replace"),
        )

        # 断言二：导出正文。按 UTF-8 字节必须恰好等于第 2、8 行原文
        # 各加一个 LF，不多不少。
        self.assertEqual(
            proc.stdout,
            EXPECTED_EXPORT_BYTES,
            "导出正文与第 2、8 行原始正文加 LF 的字节序列不一致",
        )

        # 断言三：警告内容。必须恰好且依次为第 4 行 timestamp 警告、
        # 第 7 行 JSON 解析失败警告（第 4 行级别不匹配仍须警告）。
        self.assertEqual(
            proc.stderr,
            EXPECTED_WARNING_BYTES,
            "标准警告的文案、行号、顺序或换行与既有约定不一致",
        )

        # 以下为导出正文的细化核对，均在上面字节全等的基础上补强：
        # 恰好两条记录、各一个 LF 终止符；无 CRLF。
        self.assertEqual(proc.stdout.count(b"\n"), 2)
        self.assertNotIn(b"\r\n", proc.stdout)
        # 不带行号与制表符前缀（不以“2<Tab>”开头）。
        self.assertFalse(proc.stdout.startswith(b"2\t"))
        # 不重新序列化、不加数组包装或统计字段。
        self.assertFalse(proc.stdout.startswith(b"["))
        self.assertNotIn(b"matched_count", proc.stdout)
        # 第 2、8 行各出现一次：重复的 --level ERROR 不会让第 8 行
        # （或任何 ERROR 记录）多输出；顺序保持文件物理行序。
        self.assertEqual(proc.stdout.count(LINE2.encode("utf-8")), 1)
        self.assertEqual(proc.stdout.count(LINE8.encode("utf-8")), 1)
        pos2 = proc.stdout.find(LINE2.encode("utf-8"))
        pos8 = proc.stdout.find(LINE8.encode("utf-8"))
        self.assertLess(pos2, pos8)
        # 第 8 行正文外侧的空格、制表符与中文均原样保留。
        self.assertIn(LINE8.encode("utf-8"), proc.stdout)
        self.assertTrue(proc.stdout.startswith(LINE2.encode("utf-8")))
        self.assertTrue(proc.stdout.rstrip(b"\n").endswith(LINE8.encode("utf-8")))
        # 被静默排除的行正文一律不得出现在导出中。
        for excluded in (LINE1, LINE3, LINE4, LINE5, LINE7):
            self.assertNotIn(excluded.encode("utf-8"), proc.stdout)
        # 警告中物理行号与既有文案逐字可查（顺序也由字节全等保证）。
        stderr_text = proc.stderr.decode("utf-8")
        self.assertIn("第 4 行：无效日志：timestamp 缺失或格式无效", stderr_text)
        self.assertIn("第 7 行：无效日志：JSON 解析失败", stderr_text)
        self.assertLess(
            stderr_text.find("第 4 行"),
            stderr_text.find("第 7 行"),
        )

    def test_until_equals_since_exports_nothing_but_keeps_warnings(self):
        # 合法空区间：结束时间改为起始时间，其余输入与参数完全不变。
        proc = run_cli_bytes(
            self.path,
            *LEVEL_ARGS,
            "--request-id", REQUEST_ID,
            "--since", BOUNDARY,
            "--until", BOUNDARY,
            "--jsonl",
        )

        # 断言一：退出码仍为 0（起止相等是合法空区间，不是参数错误）。
        self.assertEqual(
            proc.returncode, 0,
            "空区间场景退出码非 0；stderr="
            + proc.stderr.decode("utf-8", "replace"),
        )

        # 断言二：导出正文必须为空（含起点不含终点时没有任何记录
        # 满足 timestamp >= T 且 timestamp < T）。
        self.assertEqual(
            proc.stdout,
            b"",
            "since 与 until 相等时标准输出应为空",
        )

        # 断言三：警告不随区间变空而消失：第 4 行的 timestamp 警告与
        # 第 7 行的 JSON 解析失败警告仍按同样文案、行号与顺序输出。
        self.assertEqual(
            proc.stderr,
            EXPECTED_WARNING_BYTES,
            "空区间场景的标准警告与半开区间场景不一致",
        )

    def test_two_scenarios_share_warnings_but_differ_in_body(self):
        # 交叉核对：两个场景的退出码与警告完全一致，唯一可观测差异是
        # 导出正文（半开区间有两条，空区间为空），失败时可直接区分。
        half_open = run_cli_bytes(
            self.path,
            *LEVEL_ARGS,
            "--request-id", REQUEST_ID,
            *RANGE_ARGS,
            "--jsonl",
        )
        empty_range = run_cli_bytes(
            self.path,
            *LEVEL_ARGS,
            "--request-id", REQUEST_ID,
            "--since", BOUNDARY,
            "--until", BOUNDARY,
            "--jsonl",
        )
        self.assertEqual(half_open.returncode, empty_range.returncode)
        self.assertEqual(half_open.stderr, empty_range.stderr)
        self.assertEqual(half_open.stderr, EXPECTED_WARNING_BYTES)
        self.assertNotEqual(half_open.stdout, empty_range.stdout)
        self.assertEqual(half_open.stdout, EXPECTED_EXPORT_BYTES)
        self.assertEqual(empty_range.stdout, b"")


if __name__ == "__main__":
    unittest.main()
