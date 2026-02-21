# Cloudflare Worker Forwarder (for WebSearch MCP)

这个目录提供一个与当前仓库完全兼容的 Cloudflare Worker 转发器。  
兼容协议：`GET https://your-worker-domain/?url=<encoded_target_url>`

## 1. 为什么需要这个 Worker

当前 Python 代码会在启用 `CF_WORKER` 时，把目标地址包装为：

`{CF_WORKER}?url=<target_url>`

对应实现见：
- `websearch/utils/url_text.py:16`
- `websearch/tools/fetch_search_core.py:70`
- `websearch/tools/fetch_search_core.py:167`
- `websearch/tools/search.py:62`

## 2. 配置项与功能（wrangler.toml）

- `name`
  - Worker 名称，部署后的服务标识。
- `main`
  - 入口文件路径，当前是 `src/index.ts`。
- `compatibility_date`
  - 锁定 Workers 运行时行为，避免平台升级导致兼容变化。
- `workers_dev`
  - `true` 使用 `*.workers.dev` 快速调试；生产常设为 `false` 并配 `[[routes]]`。
- `[[routes]]` / `custom_domain`
  - 将 Worker 绑定到自定义域名路由（生产推荐）。
- `vars.ALLOWED_HOSTS`
  - 允许转发的目标域名白名单，防止开放代理被滥用。
- `vars.UPSTREAM_TIMEOUT_MS`
  - Worker 请求上游目标站点的超时时间（毫秒）。
- `vars.ENABLE_CACHE`
  - 是否启用 Cloudflare 边缘缓存（`1` 开，`0` 关）。
- `vars.CACHE_TTL_SECONDS`
  - 开启缓存后的缓存时长。
- `vars.FORWARD_COOKIES`
  - 是否向上游透传 `Cookie` 及回传 `Set-Cookie`。
- `vars.BLOCK_PRIVATE_HOSTS`
  - 是否屏蔽 `localhost` / 内网地址等私有主机请求，防 SSRF。

## 3. Worker 代码行为（src/index.ts）

- 只允许 `GET/HEAD`，降低风险。
- `GET /healthz` 返回健康检查 JSON。
- 必须带 `?url=...` 参数，否则返回 400。
- 只允许 `http/https` 目标协议。
- 目标域名必须命中 `ALLOWED_HOSTS`。
- 可选阻断私有地址访问（默认开启）。
- 自动转发请求并返回上游内容体。
- 在响应头追加：
  - `x-proxy-by: cloudflare-worker`
  - `x-proxy-target-host: <host>`

## 4. 部署步骤

1. 安装并登录 Wrangler
   - `npm i -g wrangler`
   - `wrangler login`
2. 在本目录部署
   - `cd worker`
   - `wrangler deploy`
3. 获得 Worker 地址后，配置 Python 项目 `websearch/.env`
   - `CF_WORKER=https://<your-worker-domain>`

## 5. 与当前项目的对接配置

`websearch/.env` 建议：

```env
CF_WORKER=https://your-worker-domain
PROXY=
SEARCH_TIMEOUT_S=60
FETCH_TIMEOUT_S=20
PLAYWRIGHT_FALLBACK=1
```

说明：
- `CF_WORKER`：启用 Worker 转发。
- `PROXY`：本地代理（如不需要，留空）。
- `SEARCH_TIMEOUT_S` / `FETCH_TIMEOUT_S`：客户端请求超时。
- `PLAYWRIGHT_FALLBACK`：被拦截时浏览器兜底。
