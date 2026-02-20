# WebSearch MCP Server

一个基于 MCP（Model Context Protocol）的 Web 搜索与网页抓取服务，面向 CherryStudio 等支持 MCP 的客户端。

当前版本采用 `websearch/` 作为核心包，推荐入口为 `python -m websearch`。

## 1. 项目特点

- 搜索与抓取一体化：同时提供 `web_search` 与 `fetch` 两个 MCP 工具。
- 搜索策略稳健：优先 Brave，失败时自动回退 DuckDuckGo，并对部分网络错误做重试。
- 网页抓取增强：常规提取失败时可自动回退 Playwright。
- 配置集中管理：默认从 `websearch/.env` 读取配置，支持环境变量和命令行覆盖。
- 可观测性增强：搜索结果支持返回 `diagnostics` 字段，便于排查回退与异常链路。

## 2. 项目结构

```text
.
├─ websearch/
│  ├─ __init__.py
│  ├─ __main__.py
│  ├─ .env.example
│  ├─ tools/
│  │  ├─ search.py
│  │  └─ fetch_search_core.py
│  ├─ utils/
│  │  ├─ config.py
│  │  ├─ env_parser.py
│  │  ├─ extraction.py
│  │  ├─ logger.py
│  │  ├─ noise_rules.py
│  │  ├─ rules/
│  │  │  ├─ noise_en.txt
│  │  │  └─ noise_zh.txt
│  │  └─ url_text.py
│  └─ test/
│     ├─ smoke_check.py
│     ├─ test_config_env_parser.py
│     ├─ test_config_runtime.py
│     ├─ test_retry_logic.py
│     └─ test_search_diagnostics.py
├─ WebSearch.py
├─ requirements.txt
├─ pyproject.toml
└─ README.md
```

## 3. 安装依赖

在项目根目录执行：

```bash
# 推荐
uv sync

# 或
pip install -r requirements.txt
```

## 4. 环境配置

先进入 `websearch/` 目录，复制模板配置：

```bash
cp .env.example .env
```

服务启动时会读取：`websearch/.env`

常用配置项：

```env
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

PROXY=http://127.0.0.1:7890
# CF_WORKER=https://your-worker.workers.dev

PLAYWRIGHT_FALLBACK=1
PLAYWRIGHT_TIMEOUT_MS=60000
PLAYWRIGHT_CHALLENGE_WAIT=20
PW_HEADLESS=1
PW_VIEWPORT=1366x768

SEARCH_MAX_PER_DOMAIN=2

# quality / balanced / speed
EXTRACTION_STRATEGY=quality
EXTRACTION_MARKDOWN_MIN_CHARS=120
EXTRACTION_TEXT_MIN_CHARS=200

# DEBUG / INFO / WARNING / ERROR / CRITICAL
LOG_LEVEL=INFO
```

说明：

- 仅对外暴露 `EXTRACTION_STRATEGY`、`EXTRACTION_MARKDOWN_MIN_CHARS`、`EXTRACTION_TEXT_MIN_CHARS`。
- 旧的 11 个 `EXTRACTION_*` 内部调优变量已移除，不再生效。

优先级：

- 命令行参数 > 系统环境变量 > `websearch/.env`

## 5. 启动方式

推荐方式：

```bash
uv run -m websearch
```

兼容脚本入口：

```bash
uv run WebSearch.py
```

## 6. MCP 工具

### `web_search(query: str)`

- 执行网页搜索，优先 Brave，失败时自动回退 DuckDuckGo。
- 可选返回 AI 总结（需要正确配置 OpenAI 相关变量）。
- 返回搜索结果列表，并附带 `diagnostics` 用于排查后端/回退/错误信息。

### `fetch(url: str, headers: Optional[Dict[str, str]] = None)`

- 抓取网页并提取正文 Markdown。
- 支持站点适配提取（如知乎、Discourse）。
- 遇到挑战页或拦截场景时可自动回退 Playwright。

## 7. CherryStudio 配置

在 CherryStudio 的 MCP 配置中添加：

### Windows 示例

```json
{
  "mcpServers": {
    "websearch": {
      "name": "WebSearch",
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "D:/Code/github/VibeCode/MCP/websearch",
        "run",
        "-m",
        "websearch"
      ]
    }
  }
}
```

### macOS / Linux 示例

```json
{
  "mcpServers": {
    "websearch": {
      "name": "WebSearch",
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/websearch", "run", "-m", "websearch"]
    }
  }
}
```

说明：

- `command` 使用 `uv`，通过 `--directory` 指向项目根目录。
- 确保目录下存在 `websearch/.env` 并已正确配置。

## 8. 本地自检

快速检查：

```bash
uv run -m websearch.test.smoke_check
```

严格检查（要求 LLM 配置可用）：

```bash
uv run -m websearch.test.smoke_check --require-llm
```

## 9. 常见问题

### Q1：为什么没有 AI 总结？

- 通常是 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 未配置或不可达。

### Q2：抓取内容很少或疑似被拦截？

- 保持 `PLAYWRIGHT_FALLBACK=1`。
- 必要时配置 `PROXY`。

### Q3：CherryStudio 无法启动 MCP？

- 检查 `command` 是否为 `uv`。
- 检查 `args` 是否为 `--directory ... run -m websearch`。
- 在命令行先手动执行 `uv --directory path/to/websearch run -m websearch` 验证可启动。

## Refer

此项目改编自：

- https://github.com/VonEquinox/WebSearchMCP
