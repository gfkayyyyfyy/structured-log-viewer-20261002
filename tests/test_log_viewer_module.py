"""log_viewer 模块公开函数（normalize_level / iter_matches）的契约测试。

这些测试直接调用包内函数，固定 README 与文档字符串约定的返回格式：
iter_matches 返回 (matches, warnings)，两者均为 (行号, 文本) 元组列表，
行号从 1 开始，按原文件顺序排列。
"""

import unittest

from log_viewer import SUPPORTED_LEVELS, iter_matches, normalize_level


class NormalizeLevelTests(unittest.TestCase):
    def test_strips_surrounding_whitespace_and_uppercases(self):
        self.assertEqual(normalize_level(" eRrOr "), "ERROR")
        self.assertEqual(normalize_level("\twarning\n"), "WARNING")

    def test_non_string_values_return_none(self):
        for value in (None, 123, 1.5, ["ERROR"], {"level": "ERROR"}):
            with self.subTest(value=value):
                self.assertIsNone(normalize_level(value))


class IterMatchesTests(unittest.TestCase):
    def test_returns_matches_and_warnings_as_lineno_text_pairs(self):
        lines = [
            '{"level":"INFO"}',
            '',
            'not-json',
            '{"level":" error ","message":"失败"}',
            '{"level":"ERROR","message":"第二条"}',
        ]
        matches, warnings = iter_matches(lines, "ERROR")
        self.assertEqual(matches, [
            (4, '{"level":" error ","message":"失败"}'),
            (5, '{"level":"ERROR","message":"第二条"}'),
        ])
        self.assertEqual(warnings, [(3, "无效日志：JSON 解析失败")])

    def test_blank_lines_consume_line_numbers_but_never_warn(self):
        matches, warnings = iter_matches(["", "   ", "\t"], "ERROR")
        self.assertEqual(matches, [])
        self.assertEqual(warnings, [])

    def test_valid_non_matching_record_does_not_warn(self):
        matches, warnings = iter_matches(['{"level":"INFO"}'], "ERROR")
        self.assertEqual(matches, [])
        self.assertEqual(warnings, [])

    def test_top_level_array_warns_but_later_valid_line_still_matches(self):
        lines = ["[1, 2]", '{"level":"ERROR"}']
        matches, warnings = iter_matches(lines, "ERROR")
        self.assertEqual(matches, [(2, '{"level":"ERROR"}')])
        self.assertEqual(warnings, [(1, "无效日志：顶层不是 JSON 对象")])

    def test_missing_level_warns_with_correct_line_number(self):
        matches, warnings = iter_matches(
            ['{"message":"x"}', '{"level":"ERROR"}'], "ERROR")
        self.assertEqual(matches, [(2, '{"level":"ERROR"}')])
        self.assertEqual(
            warnings, [(1, "无效日志：level 缺失或不属于支持的级别")])

    def test_non_string_level_warns_with_correct_line_number(self):
        matches, warnings = iter_matches(
            ['{"level":5}', '{"level":null}', '{"level":"ERROR"}'], "ERROR")
        self.assertEqual(matches, [(3, '{"level":"ERROR"}')])
        self.assertEqual(warnings, [
            (1, "无效日志：level 缺失或不属于支持的级别"),
            (2, "无效日志：level 缺失或不属于支持的级别"),
        ])

    def test_unsupported_level_warns(self):
        matches, warnings = iter_matches(['{"level":"TRACE"}'], "ERROR")
        self.assertEqual(matches, [])
        self.assertEqual(
            warnings, [(1, "无效日志：level 缺失或不属于支持的级别")])

    def test_five_supported_levels_match_by_normalized_equality(self):
        self.assertEqual(
            SUPPORTED_LEVELS,
            ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        )
        for level in SUPPORTED_LEVELS:
            with self.subTest(level=level):
                lines = [f'{{"level":" {level.lower()} "}}']
                matches, warnings = iter_matches(lines, level)
                self.assertEqual(matches, [(1, lines[0])])
                self.assertEqual(warnings, [])

    def test_results_keep_input_order(self):
        lines = [
            '{"level":"ERROR","message":"一"}',
            'bad',
            '{"level":"INFO"}',
            '{"level":"ERROR","message":"二"}',
        ]
        matches, warnings = iter_matches(lines, "ERROR")
        self.assertEqual(
            [lineno for lineno, _ in matches], [1, 4])
        self.assertEqual([lineno for lineno, _ in warnings], [2])

    def test_empty_input_returns_two_empty_lists(self):
        matches, warnings = iter_matches([], "ERROR")
        self.assertEqual(matches, [])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
