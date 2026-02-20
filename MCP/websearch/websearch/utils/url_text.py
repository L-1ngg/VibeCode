import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from .config import get_config

logger = logging.getLogger(__name__)


def _get_target_url(original_url: str) -> str:
    """
    如果配置了 CF Worker，则将请求包装到 Worker URL 中。
    否则返回原始 URL。
    """
    cfg = get_config()
    if cfg.cf_worker_url:
        worker_base = cfg.cf_worker_url.rstrip("/")
        encoded_target = quote(original_url)
        return f"{worker_base}?url={encoded_target}"
    return original_url


def _get_proxies() -> Optional[Dict[str, str]]:
    """
    获取代理配置。
    注意：如果使用了 CF Worker，通常不需要本地代理去连接 CF Worker（除非本地无法直连 CF）。
    这里保持逻辑：如果配置了 proxy，就让 requests/scraper 走这个 proxy。
    """
    return get_config().proxies


def _limit_content_length(content: str) -> Tuple[str, bool]:
    cfg = get_config()
    estimated_tokens = len(content) // 4
    if estimated_tokens > cfg.max_token_limit:
        chars_to_keep = cfg.max_token_limit * 4
        truncated_content = content[:chars_to_keep]
        return truncated_content, True
    return content, False


def _parse_viewport(raw: Optional[str]) -> Optional[Dict[str, int]]:
    if not raw:
        return None
    text = raw.lower().replace(" ", "")
    if "x" in text:
        width_str, height_str = text.split("x", 1)
    elif "," in text:
        width_str, height_str = text.split(",", 1)
    else:
        return None
    try:
        return {"width": int(width_str), "height": int(height_str)}
    except ValueError:
        return None


def _looks_like_challenge_text(content: str) -> bool:
    lowered = (content or "").lower()
    return (
        "just a moment" in lowered
        or "checking your browser" in lowered
        or "attention required" in lowered
        or "cf-browser-verification" in lowered
        or ("cloudflare" in lowered and "ray id" in lowered)
    )


def _looks_like_blocked_text(content: str) -> bool:
    """Detect generic blocks/login walls/captcha pages (best-effort)."""
    if not content:
        return False
    if _looks_like_challenge_text(content):
        return True

    visible_text = content
    if "<" in content and ">" in content:
        try:
            visible_text = _html_to_text(content)
        except Exception:
            visible_text = content

    lowered = (visible_text or "").lower()
    english_hints = (
        "captcha",
        "robot check",
        "access denied",
        "verify you are human",
        "unusual traffic",
    )
    for hint in english_hints:
        if hint in lowered:
            return True

    chinese_hints = (
        "访问异常",
        "安全验证",
        "滑动验证",
        "验证码",
        "请完成验证",
        "检测到异常",
        "系统检测到",
        "访问过于频繁",
        "请稍后再试",
        "请先登录",
        "登录后查看更多",
        "请登录后继续访问",
        "马上登录",
        "立即登录",
        "登录即可",
    )
    for hint in chinese_hints:
        if hint in (visible_text or ""):
            return True
    return False


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "header", "footer", "nav", "aside", "form", "button", "svg"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "igshid",
    "spm",
    "spm_id_from",
    "from",
    "from_source",
    "source",
    "sourcefrom",
    "share_source",
    "share_medium",
    "share_platform",
    "share_id",
    "share_from",
    "shareuid",
    "scene",
    "platform",
    "ref",
    "refer",
    "ref_source",
    "referrer",
    "vd_source",
    "_t",
    "_r",
    "mpshare",
}


def _normalize_url_for_dedup(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("www."):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw

    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    filtered_pairs = []
    try:
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            if key.lower() in _TRACKING_QUERY_KEYS:
                continue
            filtered_pairs.append((key, value))
    except Exception:
        filtered_pairs = []
    query = urlencode(sorted(filtered_pairs), doseq=True)

    normalized_path = path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, normalized_path, "", query, ""))


_SITE_QUERY_RE = re.compile(r"(?<!\S)site\s*:\s*([^\s]+)", re.IGNORECASE)


def _is_site_query(query: str) -> bool:
    return bool(_SITE_QUERY_RE.search(query or ""))


_REDIRECT_PARAM_CANDIDATES = ("uddg", "target", "url", "q", "u", "to", "dest", "destination", "redir", "redirect")


def _unwrap_redirect_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""

    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("www."):
        raw = "https://" + raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return raw

    netloc = (parsed.netloc or "").lower()
    path = parsed.path or ""

    try:
        params = dict(parse_qsl(parsed.query, keep_blank_values=False))
    except Exception:
        params = {}

    if netloc.endswith("duckduckgo.com") and path.startswith("/l/"):
        uddg = params.get("uddg")
        if uddg and isinstance(uddg, str) and uddg.startswith("http"):
            return uddg

    if netloc == "link.zhihu.com":
        target = params.get("target")
        if target and isinstance(target, str) and target.startswith("http"):
            return target

    if netloc.endswith("search.brave.com") and ("redirect" in path or netloc.startswith("r.")):
        target = params.get("url") or params.get("q")
        if target and isinstance(target, str) and target.startswith("http"):
            return target

    if netloc.endswith("google.com") and path.startswith("/url"):
        target = params.get("q") or params.get("url")
        if target and isinstance(target, str) and target.startswith("http"):
            return target

    if netloc.endswith("youtube.com") and path.startswith("/redirect"):
        target = params.get("q") or params.get("url")
        if target and isinstance(target, str) and target.startswith("http"):
            return target

    if netloc.endswith("steamcommunity.com") and "linkfilter" in path:
        target = params.get("url")
        if target and isinstance(target, str) and target.startswith("http"):
            return target

    if netloc == "l.facebook.com":
        target = params.get("u")
        if target and isinstance(target, str) and target.startswith("http"):
            return target

    if netloc in {"t.co"}:
        return raw

    if netloc in {"redirect.pinterest.com"}:
        for key in _REDIRECT_PARAM_CANDIDATES:
            target = params.get(key)
            if target and isinstance(target, str) and target.startswith("http"):
                return target

    return raw


def _parse_sse_chat_completions(text: str) -> Tuple[str, str]:
    content_parts: List[str] = []
    reasoning_parts: List[str] = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data:
            continue
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except Exception:
            continue

        for choice in obj.get("choices") or []:
            delta = (choice or {}).get("delta") or {}
            if not isinstance(delta, dict):
                continue
            piece = delta.get("content")
            if piece:
                content_parts.append(str(piece))
            reasoning_piece = (
                delta.get("reasoning_content")
                or delta.get("reasoning")
                or delta.get("analysis")
                or delta.get("thinking")
            )
            if reasoning_piece:
                reasoning_parts.append(str(reasoning_piece))

    return "".join(content_parts), "".join(reasoning_parts)


def _call_openai_chat_completions(prompt: str) -> Tuple[str, str]:
    cfg = get_config()
    if not cfg.openai_api_key or not cfg.openai_base_url:
        raise RuntimeError("OpenAI client not configured")

    url = f"{cfg.openai_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": cfg.openai_model,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {cfg.openai_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    response = curl_requests.post(
        url,
        json=payload,
        headers=headers,
        proxies=_get_proxies(),
        allow_redirects=True,
        impersonate=cfg.curl_impersonate,
        http_version=cfg.http_version,
        stream=True,
    )
    response.raise_for_status()

    content_type = (response.headers.get("content-type") or "").lower()
    is_sse = "text/event-stream" in content_type

    if is_sse:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        pending = ""
        done = False

        def _consume_line(raw_line: str) -> None:
            nonlocal done
            line = (raw_line or "").strip()
            if not line.startswith("data:"):
                return
            data = line[len("data:") :].strip()
            if not data:
                return
            if data == "[DONE]":
                done = True
                return
            try:
                obj = json.loads(data)
            except Exception:
                return
            for choice in obj.get("choices") or []:
                delta = (choice or {}).get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                piece = delta.get("content")
                if piece:
                    content_parts.append(str(piece))
                reasoning_piece = (
                    delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("analysis")
                    or delta.get("thinking")
                )
                if reasoning_piece:
                    reasoning_parts.append(str(reasoning_piece))

        try:
            for chunk in response.iter_content():
                if not chunk:
                    continue
                pending += chunk.decode("utf-8", errors="ignore")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    _consume_line(line.rstrip("\r"))
                    if done:
                        break
                if done:
                    break
        except Exception as e:
            if not content_parts and not reasoning_parts:
                raise
            logger.warning("AI SSE 流读取中断，返回部分结果: %s", e)
        finally:
            try:
                response.close()
            except Exception:
                pass

        if pending and not done:
            _consume_line(pending.rstrip("\r"))
        return "".join(content_parts), "".join(reasoning_parts)

    raw_chunks: List[bytes] = []
    try:
        for chunk in response.iter_content():
            if chunk:
                raw_chunks.append(chunk)
    finally:
        try:
            response.close()
        except Exception:
            pass
    text = b"".join(raw_chunks).decode("utf-8", errors="replace")

    try:
        data = json.loads(text)
    except Exception:
        return text, ""

    content = ""
    reasoning = ""
    try:
        choice0 = (data.get("choices") or [{}])[0]
        message = choice0.get("message") or {}
        content_value = message.get("content") or ""
        reasoning_value = (
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("analysis")
            or ""
        )
        if isinstance(content_value, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content_value
            )
        else:
            content = str(content_value)
        if isinstance(reasoning_value, list):
            reasoning = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in reasoning_value
            )
        else:
            reasoning = str(reasoning_value)
    except Exception:
        pass

    return content, reasoning


def _get_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _prefer_playwright_for_url(url: str) -> bool:
    host = _get_hostname(url)
    if not host:
        return False
    if host.endswith(("xiaohongshu.com", "xhslink.com")):
        return True
    if host.endswith("zhihu.com"):
        return True
    return False


def _resolve_playwright_executable_path(path: str) -> Optional[str]:
    """Work around occasional Playwright arch/path mismatches (e.g. mac-x64 vs mac-arm64)."""
    if not path:
        return None
    candidate = path
    if os.path.exists(candidate):
        return candidate

    replacements = (
        ("chrome-mac-x64", "chrome-mac-arm64"),
        ("chrome-headless-shell-mac-x64", "chrome-headless-shell-mac-arm64"),
        ("mac-x64", "mac-arm64"),
    )
    for old, new in replacements:
        if old in path:
            alt = path.replace(old, new)
            if alt != path and os.path.exists(alt):
                return alt
    return None


_ZHIHU_ANSWER_RE = re.compile(r"zhihu\.com/(?:question/\d+/)?answer/(\d+)", re.IGNORECASE)


def _extract_zhihu_answer_id(url: str) -> Optional[str]:
    match = _ZHIHU_ANSWER_RE.search(url or "")
    if match:
        return match.group(1)
    return None


def _clean_ai_tags(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r'<grok:render[^>]*>[\s\S]*?</grok:render>', '', text)
    text = re.sub(r'<[a-z_]+:[^>]+>[\s\S]*?</[a-z_]+:[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_urls(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'\1', text)
    text = re.sub(r'<(https?://[^>]+)>', '', text)
    url_pattern = r'https?://[^\s<>\"\'\)\]，。、；：）】}]+'
    text = re.sub(url_pattern, '', text)

    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'\[\s*\]', '', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(
            r"^\s*(参考来源|参考资料|参考链接|Sources|References)\b.*[:：]\s*$",
            line,
            flags=re.IGNORECASE,
        ):
            lines = lines[:index]
            break
    cleaned_lines: List[str] = []
    for line in lines:
        if re.match(r"^\s*[-*]\s*$", line):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_markdown_links(content: str, extra_text: str = "") -> Tuple[List[Dict[str, str]], str]:
    """
    从 AI 返回的内容中解析链接
    支持格式：
    1. markdown 链接 [title](url)
    2. 纯 URL https://...
    3. 带括号的链接 (https://...)
    返回：(链接列表, 清理后的分析内容)
    """
    content = content or ""
    extra_text = extra_text or ""
    link_source = f"{content}\n{extra_text}" if extra_text else content
    links = []
    seen_urls = set()

    def _normalize_candidate(raw_url: str) -> str:
        url = (raw_url or "").strip()
        if not url:
            return ""
        url = re.sub(r"[\s\)\]\}>,，。、；：]+$", "", url)
        url = re.sub(r"[.,;:!?]+$", "", url)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("www."):
            url = "https://" + url
        url = _unwrap_redirect_url(url)
        return url.strip()

    md_pattern = r'\[([^\]]+)\]\(((?:https?://|//|www\.)[^)\s]+)\)'
    for match in re.finditer(md_pattern, link_source):
        title = match.group(1).strip()
        url = _normalize_candidate(match.group(2))
        if not url.startswith("http"):
            continue
        dedup_key = _normalize_url_for_dedup(url) or url
        if dedup_key not in seen_urls:
            seen_urls.add(dedup_key)
            links.append({"title": title, "url": url, "description": ""})

    content_without_md = re.sub(md_pattern, '', link_source)
    url_pattern = r'(?:https?://|//|www\.)[^\s<>\"\'\)\]，。、；：）】}]+'
    for match in re.finditer(url_pattern, content_without_md):
        url = _normalize_candidate(match.group(0))
        if not url.startswith("http") or len(url) <= 10:
            continue
        dedup_key = _normalize_url_for_dedup(url) or url
        if dedup_key not in seen_urls:
            seen_urls.add(dedup_key)
            title = url.split('//')[-1].split('/')[0]
            links.append({"title": title, "url": url, "description": ""})

    json_url_pattern = r'"url"\s*:\s*"([^"]+)"'
    for match in re.finditer(json_url_pattern, link_source):
        url = _normalize_candidate(match.group(1))
        if not url.startswith("http") or len(url) <= 10:
            continue
        dedup_key = _normalize_url_for_dedup(url) or url
        if dedup_key not in seen_urls:
            seen_urls.add(dedup_key)
            title = url.split("//")[-1].split("/")[0]
            links.append({"title": title, "url": url, "description": ""})

    summary_patterns = [
        r'###\s*详细总结分析([\s\S]*)',
        r'###\s*总结分析([\s\S]*)',
        r'##\s*总结([\s\S]*)',
        r'####\s*结论([\s\S]*)',
    ]

    summary = ""
    summary_source = content.strip() or link_source
    for pattern in summary_patterns:
        match = re.search(pattern, summary_source)
        if match:
            summary = match.group(0).strip()
            break

    if not summary:
        summary = summary_source

    summary = _clean_ai_tags(summary)

    return links, summary


def _extract_browse_page_links(content: str, extra_text: str = "") -> List[Dict[str, str]]:
    """Extract URLs from Grok-style tool trace lines:
    browse_page {"url":"https://...","instructions":"..."}
    """
    source = f"{content}\n{extra_text}" if extra_text else (content or "")
    if not source:
        return []

    links: List[Dict[str, str]] = []
    seen: set[str] = set()

    browse_pattern = re.compile(
        r"browse_page\s*\{\s*\"url\"\s*:\s*\"((?:[^\"\\]|\\.)+)\"(?:\s*,\s*\"instructions\"\s*:\s*\"((?:[^\"\\]|\\.)*)\")?\s*\}",
        flags=re.IGNORECASE,
    )

    def _unescape_json_fragment(value: str) -> str:
        raw = value or ""
        raw = raw.replace("\\/", "/")
        raw = raw.replace('\\"', '"')
        return raw

    for match in browse_pattern.finditer(source):
        raw_url = _unescape_json_fragment(match.group(1).strip())
        instruction = _unescape_json_fragment((match.group(2) or "").strip())
        url = _unwrap_redirect_url(raw_url)
        if not url or not url.startswith("http"):
            continue
        key = _normalize_url_for_dedup(url) or url
        if key in seen:
            continue
        seen.add(key)
        title = f"browse_page: {instruction[:80].strip()}" if instruction else url.split("//")[-1].split("/")[0]
        links.append({"title": title, "url": url, "description": ""})

    return links
