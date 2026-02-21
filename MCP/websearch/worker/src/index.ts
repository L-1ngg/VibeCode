export interface Env {
  ALLOWED_HOSTS?: string;
  UPSTREAM_TIMEOUT_MS?: string;
  ENABLE_CACHE?: string;
  CACHE_TTL_SECONDS?: string;
  FORWARD_COOKIES?: string;
  BLOCK_PRIVATE_HOSTS?: string;
}

const DEFAULT_ALLOWED_HOSTS = [
  "search.brave.com",
  "duckduckgo.com",
  "zhihu.com",
  "www.zhihu.com",
];

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
]);

function parseBool(raw: string | undefined, fallback: boolean): boolean {
  if (raw == null) return fallback;
  const v = raw.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(v)) return true;
  if (["0", "false", "no", "off"].includes(v)) return false;
  return fallback;
}

function parseIntWithFloor(raw: string | undefined, fallback: number, min: number): number {
  if (raw == null) return fallback;
  const n = Number.parseInt(raw.trim(), 10);
  if (!Number.isFinite(n) || Number.isNaN(n)) return fallback;
  if (n < min) return fallback;
  return n;
}

function normalizeHost(host: string): string {
  return host.trim().toLowerCase().replace(/\.$/, "");
}

function parseAllowedHosts(raw: string | undefined): string[] {
  const source = raw?.trim() ? raw : DEFAULT_ALLOWED_HOSTS.join(",");
  return source
    .split(",")
    .map((x) => normalizeHost(x))
    .filter(Boolean);
}

function isIPv4(host: string): boolean {
  return /^(\d{1,3}\.){3}\d{1,3}$/.test(host);
}

function isPrivateIPv4(host: string): boolean {
  if (!isIPv4(host)) return false;
  const parts = host.split(".").map((x) => Number.parseInt(x, 10));
  if (parts.some((x) => Number.isNaN(x) || x < 0 || x > 255)) return false;
  const [a, b] = parts;
  if (a === 10) return true;
  if (a === 127) return true;
  if (a === 0) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

function isPrivateHostname(host: string): boolean {
  const h = normalizeHost(host);
  if (!h) return true;
  if (h === "localhost" || h.endsWith(".localhost") || h.endsWith(".local")) return true;
  if (h === "::1") return true;
  if (h.startsWith("fc") || h.startsWith("fd")) return true;
  if (isPrivateIPv4(h)) return true;
  return false;
}

function isHostAllowed(host: string, allowedHosts: string[]): boolean {
  const h = normalizeHost(host);
  if (!h || allowedHosts.length === 0) return false;

  for (const rule of allowedHosts) {
    if (rule === "*") return true;
    if (rule.startsWith("*.")) {
      const base = rule.slice(2);
      if (!base) continue;
      if (h === base || h.endsWith(`.${base}`)) return true;
      continue;
    }
    if (h === rule) return true;
  }

  return false;
}

function buildForwardHeaders(request: Request, forwardCookies: boolean): Headers {
  const out = new Headers();
  request.headers.forEach((value, key) => {
    const k = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(k)) return;
    if (k.startsWith("cf-")) return;
    if (k === "x-forwarded-for" || k === "x-forwarded-host" || k === "x-forwarded-proto") return;
    if (!forwardCookies && k === "cookie") return;
    out.set(key, value);
  });
  return out;
}

function buildJson(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const requestUrl = new URL(request.url);
    if (requestUrl.pathname === "/healthz") {
      return buildJson(200, { ok: true, service: "websearch-forwarder" });
    }

    if (!["GET", "HEAD"].includes(request.method)) {
      return buildJson(405, { error: "method_not_allowed", allowed: ["GET", "HEAD"] });
    }

    const rawTarget = requestUrl.searchParams.get("url");
    if (!rawTarget) {
      return buildJson(400, { error: "missing_url_param", hint: "Use ?url=https%3A%2F%2Fexample.com" });
    }

    let target: URL;
    try {
      target = new URL(rawTarget);
    } catch {
      return buildJson(400, { error: "invalid_target_url" });
    }

    if (!["http:", "https:"].includes(target.protocol)) {
      return buildJson(400, { error: "unsupported_protocol", protocol: target.protocol });
    }

    if (target.username || target.password) {
      return buildJson(400, { error: "credential_in_url_not_allowed" });
    }

    const host = normalizeHost(target.hostname);
    const allowedHosts = parseAllowedHosts(env.ALLOWED_HOSTS);
    const blockPrivate = parseBool(env.BLOCK_PRIVATE_HOSTS, true);
    if (blockPrivate && isPrivateHostname(host)) {
      return buildJson(403, { error: "private_host_blocked", host });
    }
    if (!isHostAllowed(host, allowedHosts)) {
      return buildJson(403, { error: "host_not_allowed", host });
    }

    const forwardCookies = parseBool(env.FORWARD_COOKIES, false);
    const headers = buildForwardHeaders(request, forwardCookies);

    const timeoutMs = parseIntWithFloor(env.UPSTREAM_TIMEOUT_MS, 20_000, 1000);
    const cacheEnabled = parseBool(env.ENABLE_CACHE, false);
    const cacheTtl = parseIntWithFloor(env.CACHE_TTL_SECONDS, 120, 1);

    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort("upstream_timeout"), timeoutMs);

    try {
      const upstream = await fetch(target.toString(), {
        method: request.method,
        headers,
        redirect: "follow",
        signal: controller.signal,
        // `cf` is Cloudflare-specific RequestInit extension.
        cf: cacheEnabled ? { cacheEverything: true, cacheTtl } : undefined,
      } as RequestInit);

      const responseHeaders = new Headers(upstream.headers);
      if (!forwardCookies) responseHeaders.delete("set-cookie");
      responseHeaders.set("x-proxy-by", "cloudflare-worker");
      responseHeaders.set("x-proxy-target-host", host);

      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return buildJson(504, { error: "upstream_fetch_failed", message, host, timeoutMs });
    } finally {
      clearTimeout(timeoutHandle);
    }
  },
};
