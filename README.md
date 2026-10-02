# 本地结构化日志查看器

建设面向开发者的本地日志分析产品，逐步覆盖 JSONL 与简单文本日志导入、级别和时间筛选、请求标识关联、统计摘要、文件轮转处理、错误记录定位和结果导出。

计划采用：Python 3 标准库 / json / re / sqlite3 / argparse。

## 当前功能：JSONL 级别筛选查看器

仅依赖 Python 3 标准库，无需联网。

```bash
python -m log_viewer <日志文件路径> --level <级别>
```

- `路径` 与 `--level` 均为必需；级别支持 DEBUG、INFO、WARNING、ERROR、CRITICAL，忽略大小写和首尾空白，只做相等匹配。
- 输入文件须为 UTF-8 编码的 JSONL（支持 LF/CRLF），每个非空行是含字符串 `level` 字段的 JSON 对象。
- 匹配行按原顺序输出到标准输出，格式为 `行号<Tab>原始行`（行号从 1 开始按物理行计算）。
- 空白行直接跳过；JSON 损坏、顶层非对象、`level` 缺失/非字符串/不属于支持级别的行视为无效日志，在标准错误输出含行号的警告，不影响退出码。
- 正常完成退出码为 0（含无匹配、存在无效行的情况）；参数缺失/级别无效、文件不存在/是目录/不可读/非合法 UTF-8 时退出码为 2，标准输出为空。

### 示例

仓库自带 `sample.jsonl`：

```
{"level":"INFO","message":"开始"}
（空行）
not-json
{"level":" error ","message":"失败"}
```

运行：

```bash
python -m log_viewer sample.jsonl --level ERROR
```

标准输出仅第四行：

```
4	{"level":" error ","message":"失败"}
```

标准错误提示第三行无效：`第 3 行：无效日志，已跳过`。把路径换成不存在的文件则得到文件读取失败提示并以退出码 2 结束。
