# 测试执行说明

本目录为 `python -m log_viewer` 既有命令行流程及模块公开函数提供离线回归测试。

## 运行方式

在**项目根目录**（包含 `log_viewer/` 与 `tests/` 的目录）执行：

```
python -m unittest discover -s tests
```

- 全部通过：退出码为 `0`。
- 存在断言失败或错误：退出码非零，并在输出中指出失败的具体用例与所在文件、行号。
- 可加 `-v` 查看逐条用例：`python -m unittest discover -s tests -v`

## 测试范围

- `test_log_viewer_cli.py`：以子进程运行 `python -m log_viewer`，断言**标准输出、标准错误和进程退出码**。
  - 核心示例（INFO / 空白行 / not-json / 带空格的 error / ERROR 五行）配合 `--level " eRrOr "`：
    标准输出仅为第 4、5 行（`行号<Tab>原文`，保持原文件顺序），标准错误仅有第 3 行的无效日志警告，退出码 0。
  - LF、CRLF、末行无换行符三种物理行格式结果一致。
  - 匹配行首尾空格与中文内容原样保留。
  - 顶层数组、缺少 level、非字符串 level、不支持的级别：每个无效非空行各产生一条含正确行号的警告，后续有效行仍输出。
  - 五种级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）的规范化相等匹配。
  - 空文件、仅空白行、全部不匹配：两个输出流均为空，退出码 0。
  - 仅含无效行：标准输出为空，警告按行号递增，退出码 0。
  - 缺少 `--level`、传入不支持的级别：标准输出为空，标准错误含参数错误信息，退出码 2。
  - 路径不存在、路径是目录、文件含非法 UTF-8 字节：标准输出为空，标准错误含“文件读取失败”信息，退出码 2（不断言操作系统附加的错误细节）。
- `test_log_viewer_module.py`：直接调用 `normalize_level` 与 `iter_matches`，固定空白行占行号不警告、
  无效行分类警告文案、匹配/警告均为 `(行号, 文本)` 列表且保持输入顺序等返回约定。

## 依赖

仅使用 Python 3 标准库（`unittest`、`subprocess`、`tempfile`、`pathlib` 等）。
测试输入在临时目录中现场生成，不依赖 `sample.jsonl`，也不访问任何外部服务或网络。
