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
│  │  ├─ search.py              # MCP 工具入口：web_search / fetch
│  │  └─ fetch_search_core.py   # 搜索引擎抓取、Playwright 回退、重试逻辑
│  ├─ utils/
│  │  ├─ config.py              # 集中配置管理（AppConfig / PlaywrightConfig / ExtractionConfig）
│  │  ├─ env_parser.py          # .env 文件解析
│  │  ├─ extraction.py          # HTML 正文提取、质量评分、站点适配器（CSDN/GitHub/Discourse 等）
│  │  ├─ noise_rules.py         # 噪声行规则加载
│  │  ├─ url_helpers.py         # URL 归一化、重定向解包、hostname 工具
│  │  ├─ proxy.py               # 代理配置、Cloudflare Worker URL 拼接
│  │  ├─ openai_client.py       # OpenAI 兼容 Chat Completions 客户端
│  │  ├─ html_detect.py         # 反爬/Challenge 页面检测、HTML→纯文本
│  │  ├─ content_parse.py       # 内容截断、Markdown 链接解析、URL/AI标签清理
│  │  ├─ url_text.py            # 向后兼容 re-export shim（已拆分至上述模块）
│  │  └─ rules/
│  │     ├─ noise_en.txt
│  │     └─ noise_zh.txt
│  └─ test/
│     ├─ smoke_check.py
│     ├─ test_config_env_parser.py
│     ├─ test_config_runtime.py
│     ├─ test_extraction_config_surface.py
│     ├─ test_retry_logic.py
│     └─ test_search_diagnostics.py
├─ worker/                       # Cloudflare Worker 转发模板
├─ WebSearch.py
├─ pyproject.toml
└─ README.md
```

### 模块职责说明

| 模块                   | 职责                                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------- |
| `config.py`            | 从 `.env` / 环境变量 / 命令行统一解析所有配置，暴露 `AppConfig` 冻结数据类                |
| `url_helpers.py`       | URL 去重归一化、跟踪参数清理、重定向解包、`site:` 查询检测                                |
| `proxy.py`             | 根据配置生成 `proxies` 字典、拼接 Cloudflare Worker 转发 URL                              |
| `openai_client.py`     | 调用 OpenAI 兼容 API（支持 SSE 流式），用于 AI 搜索摘要                                   |
| `html_detect.py`       | 检测反爬拦截页 / Cloudflare Challenge、HTML→纯文本转换                                    |
| `content_parse.py`     | 内容长度截断、从 AI 回复中解析 Markdown 链接和 SOURCES 块                                 |
| `extraction.py`        | 多策略正文提取（trafilatura precision/recall/fast/baseline + 站点适配器），质量评分与排序 |
| `fetch_search_core.py` | Brave/DuckDuckGo 搜索引擎抓取、curl 重试、知乎 API 适配、Playwright 浏览器回退            |
| `search.py`            | MCP 工具注册入口，编排 AI 搜索 + 浏览器搜索并发、结果去重合并                             |

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

### Cloudflare Worker 转发模板

仓库已提供可直接部署的 Worker 模板：

- `worker/wrangler.toml`
- `worker/src/index.ts`
- `worker/README.md`

启用后，在 `websearch/.env` 中设置：

```env
CF_WORKER=https://your-worker-domain
```

### Cloudflare Worker + Wrangler 部署指南

以下步骤用于把 `worker/` 部署为可用的转发服务，并接入当前 MCP。

#### 1) 准备 Wrangler

任选一种方式安装：

```bash
# npm
npm i -g wrangler

# bun（推荐本项目已有 bun 环境时）
bun add -g wrangler
```

检查版本：

```bash
wrangler --version
# 或
bun wrangler --version
```

登录 Cloudflare：

```bash
wrangler login
# 或
bun wrangler login
```

检查登录状态：

```bash
wrangler whoami
# 或
bun wrangler whoami
```

#### 2) 部署 Worker

进入 Worker 目录并部署：

```bash
cd worker
wrangler deploy
# 或
bun wrangler deploy
```

部署成功后会得到一个 `https://<name>.<subdomain>.workers.dev` 地址。

#### 3) 验证 Worker 可用性

```bash
# 健康检查
curl "https://<your-worker>.workers.dev/healthz"

# 缺少 url 参数（应返回 400）
curl "https://<your-worker>.workers.dev/"

# 协议拦截（应返回 400）
curl "https://<your-worker>.workers.dev/?url=ftp%3A%2F%2Fexample.com"

# 白名单内域名（应成功）
curl "https://<your-worker>.workers.dev/?url=https%3A%2F%2Fduckduckgo.com%2Fhtml%2F%3Fq%3Dcloudflare"
```

如果你的本机网络需要代理，请使用：

```bash
curl --proxy http://127.0.0.1:xxx "https://<your-worker>.workers.dev/healthz"
```

#### 4) 接入 MCP 配置

在 `websearch/.env` 设置：

```env
CF_WORKER=https://<your-worker>.workers.dev
```

建议同时确认：

```env
SEARCH_TIMEOUT_S=60
FETCH_TIMEOUT_S=20
PLAYWRIGHT_FALLBACK=1
```

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

### `fetch(url: str, headers: dict[str, str] | None = None)`

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
      "args": ["--directory", "/path/to/websearch", "run", "-m", "websearch"]
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
