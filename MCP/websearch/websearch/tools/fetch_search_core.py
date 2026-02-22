from __future__ import annotations

import asyncio
import html as html_lib
import logging
import os
import re
import time
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlparse, urlunparse

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from ..utils.config import AppConfig, get_config
from ..utils.content_parse import limit_content_length
from ..utils.extraction import (
    _build_degraded_markdown,
    _build_degraded_text,
    _clean_extracted_markdown,
    _clean_extracted_text,
    _extract_best_content,
    _extract_metadata,
    _score_content,
    _trafilatura_extract,
)
from ..utils.html_detect import (
    html_to_text,
    looks_like_blocked_text,
    looks_like_challenge_text,
)
from ..utils.proxy import get_proxies, get_target_url
from ..utils.url_helpers import (
    extract_zhihu_answer_id,
    resolve_playwright_executable_path,
)

logger = logging.getLogger(__name__)

_CURL_ERROR_CODE_RE = re.compile(r"curl:\s*\((\d+)\)")
_CURL_RETRYABLE_CODES = {18, 23, 28}
_CURL_RETRYABLE_HINTS = (
    "Failed reading the chunked-encoded stream",
    "Operation timed out",
    "transfer closed with",
)


def _default_headers(cfg: AppConfig) -> dict[str, str]:
    return {
        "User-Agent": cfg.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close",
    }


def _classify_curl_exception(error: Exception) -> tuple[int | None, bool]:
    message = str(error)
    code: int | None = None
    match = _CURL_ERROR_CODE_RE.search(message)
    if match:
        try:
            code = int(match.group(1))
        except Exception:
            code = None

    retryable = (code in _CURL_RETRYABLE_CODES) or any(hint in message for hint in _CURL_RETRYABLE_HINTS)
    return code, retryable


async def _search_brave_core(
    query: str,
    max_results: int = 20,
) -> list[dict[str, str]]:
    def _fetch_and_parse() -> list[dict[str, str]]:
        cfg = get_config()
        target_url = f"https://search.brave.com/search?q={quote_plus(query)}"
        visit_url = get_target_url(target_url)

        logger.info("正在搜索: %s", query)
        if cfg.cf_worker_url:
            logger.info("Via Cloudflare Worker: %s", visit_url)

        request_headers = _default_headers(cfg)

        response = curl_requests.get(
            visit_url,
            headers=request_headers,
            proxies=get_proxies(),
            timeout=cfg.search_timeout_s,
            allow_redirects=True,
            impersonate=cfg.curl_impersonate,
            http_version=cfg.http_version,
        )
        response.raise_for_status()
        html = response.text or ""
        results = _extract_brave_results(html, max_results, cfg)
        logger.info("搜索完成，找到 %s 个结果", len(results))
        return results

    def _extract_brave_results(html: str, limit: int, cfg: AppConfig) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        items = soup.select('[data-type="web"]')
        if not items:
            items = soup.select(".snippet")

        if limit and limit > 0:
            items = items[:limit]

        extracted: list[dict[str, str]] = []
        for item in items:
            link = item.select_one("a[href]")
            if not link:
                continue
            href = link.get("href", "")
            if not href.startswith("http"):
                continue
            if cfg.cf_worker_url and cfg.cf_worker_url in href:
                continue

            title_elem = item.select_one(".snippet-title, .title")
            desc_elem = item.select_one(".snippet-description, .snippet-content, .description")

            extracted.append(
                {
                    "title": title_elem.get_text(strip=True) if title_elem else "No Title",
                    "url": href,
                    "description": desc_elem.get_text(strip=True) if desc_elem else "",
                }
            )
        return extracted

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_and_parse)


async def _search_duckduckgo_core(
    query: str,
    max_results: int = 20,
) -> list[dict[str, str]]:
    try:

        def _decode_ddg_url(href: str) -> str:
            if not href:
                return ""
            if href.startswith("//"):
                href = "https:" + href
            if href.startswith("/"):
                href = "https://duckduckgo.com" + href
            if not href.startswith("http"):
                return ""
            try:
                parsed = urlparse(href)
                if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
                    params = dict(parse_qsl(parsed.query))
                    uddg = params.get("uddg")
                    if uddg:
                        return uddg
            except Exception:
                return href
            return href

        def _fetch_and_parse() -> list[dict[str, str]]:
            cfg = get_config()
            target_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
            visit_url = get_target_url(target_url)

            logger.info("正在搜索(DDG): %s", query)
            if cfg.cf_worker_url:
                logger.info("Via Cloudflare Worker: %s", visit_url)

            request_headers = _default_headers(cfg)

            response = _curl_get_with_retries(
                visit_url,
                headers=request_headers,
                timeout_s=cfg.search_timeout_s,
                retries=3,
            )
            html = response.text or ""

            soup = BeautifulSoup(html, "lxml")
            results: list[dict[str, str]] = []
            for item in soup.select(".results .result"):
                link = item.select_one("a.result__a[href]")
                if not link:
                    continue
                href = _decode_ddg_url(link.get("href", ""))
                if not href.startswith("http"):
                    continue
                title = link.get_text(strip=True) or "No Title"
                desc_elem = item.select_one(".result__snippet") or item.select_one(".result__body")
                desc = desc_elem.get_text(strip=True) if desc_elem else ""
                results.append({"title": title, "url": href, "description": desc})
                if max_results and len(results) >= max_results:
                    break
            logger.info("搜索完成(DDG)，找到 %s 个结果", len(results))
            return results

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch_and_parse)
    except Exception as e:
        logger.error("DDG 搜索过程发生错误: %s", e)
        return []


def _curl_get_with_retries(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_s: int | None = None,
    retries: int = 2,
) -> curl_requests.Response:
    cfg = get_config()
    effective_timeout_s = timeout_s if timeout_s is not None else cfg.fetch_timeout_s
    request_headers = {**_default_headers(cfg), **(headers or {})}

    max_attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = curl_requests.get(
                url,
                headers=request_headers,
                proxies=get_proxies(),
                timeout=effective_timeout_s,
                allow_redirects=True,
                impersonate=cfg.curl_impersonate,
                http_version=cfg.http_version,
            )
            response.raise_for_status()
            return response
        except Exception as e:
            last_error = e
            code, retryable = _classify_curl_exception(e)
            message = str(e).replace("\n", " ").strip()
            if len(message) > 240:
                message = f"{message[:237]}..."

            can_retry = retryable and attempt < max_attempts
            if not can_retry:
                logger.debug(
                    "HTTP FAIL attempt=%s/%s url=%s timeout_s=%s code=%s err=%s",
                    attempt,
                    max_attempts,
                    url,
                    effective_timeout_s,
                    code,
                    message,
                )
                raise

            logger.warning(
                "HTTP RETRY attempt=%s/%s url=%s timeout_s=%s next_timeout_s=%s code=%s err=%s",
                attempt,
                max_attempts,
                url,
                effective_timeout_s,
                max(effective_timeout_s * 2, effective_timeout_s + 10),
                code,
                message,
            )
            effective_timeout_s = max(effective_timeout_s * 2, effective_timeout_s + 10)
            time.sleep(0.3 * attempt)
    assert last_error is not None
    raise last_error


def _fetch_zhihu_answer_content(url: str, mode: str) -> dict[str, Any] | None:
    answer_id = extract_zhihu_answer_id(url)
    if not answer_id:
        return None

    api_url = (
        "https://www.zhihu.com/api/v4/answers/"
        f"{answer_id}?include=content,excerpt,content_need_truncated,segment_infos"
    )

    def _build_result(content_html: str, via_worker: bool) -> dict[str, Any]:
        if mode == "html":
            limited_html, was_truncated = limit_content_length(content_html)
            return {
                "success": True,
                "url": url,
                "via_worker": via_worker,
                "via_playwright": False,
                "via_zhihu_api": True,
                "html": limited_html,
                "truncated": was_truncated,
            }
        wrapped_html = f"<html><body>{content_html}</body></html>"
        if mode == "markdown":
            extracted = _extract_best_content(wrapped_html, url=url, output_format="markdown")
            limited_md, was_truncated = limit_content_length(extracted.get("content", ""))
            return {
                "success": True,
                "url": url,
                "via_worker": via_worker,
                "via_playwright": False,
                "via_zhihu_api": True,
                "markdown": limited_md,
                "truncated": was_truncated,
                "extractor": extracted.get("extractor"),
                "quality_score": extracted.get("quality_score"),
                "quality_metrics": {
                    "char_len": extracted.get("char_len"),
                    "line_count": extracted.get("line_count"),
                    "unique_line_ratio": extracted.get("unique_line_ratio"),
                    "noise_line_ratio": extracted.get("noise_line_ratio"),
                },
                "degraded": extracted.get("degraded", False),
            }

        extracted = _extract_best_content(wrapped_html, url=url, output_format="txt")
        limited_text, was_truncated = limit_content_length(extracted.get("content", ""))
        return {
            "success": True,
            "url": url,
            "via_worker": via_worker,
            "via_playwright": False,
            "via_zhihu_api": True,
            "text": limited_text,
            "truncated": was_truncated,
            "extractor": extracted.get("extractor"),
            "quality_score": extracted.get("quality_score"),
            "quality_metrics": {
                "char_len": extracted.get("char_len"),
                "line_count": extracted.get("line_count"),
                "unique_line_ratio": extracted.get("unique_line_ratio"),
                "noise_line_ratio": extracted.get("noise_line_ratio"),
            },
            "degraded": extracted.get("degraded", False),
        }

    def _try_fetch(target_url: str, via_worker: bool) -> dict[str, Any] | None:
        response = _curl_get_with_retries(
            target_url,
            headers={"Accept": "application/json"},
            timeout_s=get_config().fetch_timeout_s,
            retries=2,
        )
        try:
            data = response.json()
        except Exception:
            return None
        content_html = data.get("content") if isinstance(data, dict) else None
        if not content_html:
            return None
        if data.get("content_need_truncated") and data.get("segment_infos"):
            compact_content = re.sub(r"\s+", "", content_html)
            extra_parts = []
            for segment in data.get("segment_infos") or []:
                text = (segment or {}).get("text") or ""
                if not text.strip():
                    continue
                compact_text = re.sub(r"\s+", "", text)
                if compact_text and compact_text[:20] in compact_content:
                    continue
                extra_parts.append(f"<p>{html_lib.escape(text.strip())}</p>")
            if extra_parts:
                content_html = content_html + "".join(extra_parts)
        return _build_result(content_html, via_worker)

    if get_config().cf_worker_url:
        try:
            result = _try_fetch(get_target_url(api_url), True)
        except Exception:
            result = None
        if result:
            return result
        try:
            return _try_fetch(api_url, False)
        except Exception:
            return None
    try:
        return _try_fetch(api_url, False)
    except Exception:
        return None


def _discourse_topic_json_url(url: str) -> str | None:
    parsed = urlparse(url or "")
    if not parsed.netloc:
        return None
    path = parsed.path or ""
    if path.endswith(".json"):
        return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))

    segments = [seg for seg in path.split("/") if seg]
    if "t" not in segments:
        return None

    t_index = segments.index("t")
    topic_id_index: int | None = None
    for i in range(t_index + 1, len(segments)):
        if segments[i].isdigit():
            topic_id_index = i
            break
    if topic_id_index is None:
        return None

    json_path = "/" + "/".join(segments[: topic_id_index + 1]) + ".json"
    return urlunparse((parsed.scheme or "https", parsed.netloc, json_path, "", "", ""))


def _extract_discourse_topic_markdown(data: Any, *, url: str) -> str | None:
    if not isinstance(data, dict):
        return None
    title = (data.get("title") or "").strip()
    post_stream = data.get("post_stream") if isinstance(data.get("post_stream"), dict) else {}
    posts = post_stream.get("posts") if isinstance(post_stream.get("posts"), list) else []
    if not posts:
        return None

    parts: list[str] = []
    if title:
        parts.append(f"# {title}")

    for post in posts:
        if not isinstance(post, dict):
            continue
        cooked = post.get("cooked") or ""
        cooked = cooked.strip()
        if not cooked:
            continue
        username = (post.get("username") or "").strip()
        post_number = post.get("post_number")
        if username:
            header = f"## {username}"
            if isinstance(post_number, int):
                header = f"{header} · #{post_number}"
            parts.append(header)

        wrapped = f"<html><body>{cooked}</body></html>"
        md = _trafilatura_extract(
            wrapped,
            url=url,
            output_format="markdown",
            favor_precision=True,
            include_links=True,
        )
        if not md:
            md = html_to_text(wrapped)
        md = _clean_extracted_markdown(md)
        if md:
            parts.append(md)

    combined = "\n\n".join([p for p in parts if p and p.strip()]).strip()
    return combined or None


def _fetch_discourse_topic_content(url: str) -> dict[str, Any] | None:
    json_url = _discourse_topic_json_url(url)
    if not json_url:
        return None

    try:
        response = _curl_get_with_retries(
            get_target_url(json_url),
            headers={"Accept": "application/json"},
            timeout_s=get_config().fetch_timeout_s,
            retries=2,
        )
    except Exception:
        return None

    raw = response.text or ""
    if looks_like_blocked_text(raw):
        return None

    try:
        data = response.json()
    except Exception:
        return None

    markdown = _extract_discourse_topic_markdown(data, url=url)
    if not markdown:
        return None

    limited_md, was_truncated = limit_content_length(markdown)
    metrics = _score_content(limited_md)
    return {
        "success": True,
        "url": url,
        "via_worker": bool(get_config().cf_worker_url),
        "via_playwright": False,
        "status_code": response.status_code,
        "markdown": limited_md,
        "truncated": was_truncated,
        "blocked": False,
        "extractor": "adapter:discourse:topic_json",
        "quality_score": metrics.get("quality_score"),
        "quality_metrics": {
            "char_len": metrics.get("char_len"),
            "line_count": metrics.get("line_count"),
            "unique_line_ratio": metrics.get("unique_line_ratio"),
            "noise_line_ratio": metrics.get("noise_line_ratio"),
        },
        "degraded": False,
    }


def _extract_and_build_result(
    html: str, url: str, mode: str, blocked: bool,
) -> dict[str, Any]:
    """Shared extraction for playwright text/markdown modes."""
    fmt = "markdown" if mode == "markdown" else "txt"
    content_key = "markdown" if mode == "markdown" else "text"
    extracted = _extract_best_content(html, url=url, output_format=fmt)

    if blocked and extracted.get("quality_score", 0) < 65:
        if fmt == "markdown":
            degraded = _build_degraded_markdown(html) or ""
            degraded = _clean_extracted_markdown(degraded)
        else:
            degraded = _build_degraded_text(html) or ""
            degraded = _clean_extracted_text(degraded)
        metrics = _score_content(degraded)
        extracted = {"content": degraded, "extractor": "meta:blocked", "degraded": True, **metrics}

    limited, was_truncated = limit_content_length(extracted.get("content", ""))
    return {
        "success": True,
        "url": url,
        "via_worker": False,
        "via_playwright": True,
        content_key: limited,
        "truncated": was_truncated,
        "blocked": blocked,
        "extractor": extracted.get("extractor"),
        "quality_score": extracted.get("quality_score"),
        "quality_metrics": {
            "char_len": extracted.get("char_len"),
            "line_count": extracted.get("line_count"),
            "unique_line_ratio": extracted.get("unique_line_ratio"),
            "noise_line_ratio": extracted.get("noise_line_ratio"),
        },
        "degraded": extracted.get("degraded", False),
    }


async def _fetch_with_playwright(
    url: str,
    mode: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.playwright_fallback:
        return {"success": False, "url": url, "error": "Playwright fallback disabled"}

    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except Exception as e:
        return {"success": False, "url": url, "error": f"Playwright not available: {e}"}

    pw = cfg.playwright
    headless = pw.headless
    user_agent = pw.user_agent
    accept_language = pw.accept_language
    locale = pw.locale
    timezone_id = pw.timezone_id
    viewport = pw.viewport
    device_scale_factor = pw.device_scale_factor

    extra_headers: dict[str, str] = {"Accept-Language": accept_language}
    if headers:
        for key, value in headers.items():
            if key.lower() == "user-agent":
                user_agent = value
            elif key.lower() == "accept-language":
                extra_headers["Accept-Language"] = value
            else:
                extra_headers[key] = value

    context_kwargs: dict[str, Any] = {
        "user_agent": user_agent,
        "locale": locale,
        "timezone_id": timezone_id,
        "color_scheme": "light",
        "device_scale_factor": device_scale_factor,
    }
    if viewport:
        context_kwargs["viewport"] = viewport

    try:
        launch_args: dict[str, Any] = {"headless": headless}
        if cfg.proxy:
            launch_args["proxy"] = {"server": cfg.proxy}

        async with async_playwright() as p:
            if pw.executable_path and os.path.exists(pw.executable_path):
                launch_args["executable_path"] = pw.executable_path
            else:
                resolved = resolve_playwright_executable_path(getattr(p.chromium, "executable_path", ""))
                if resolved:
                    launch_args["executable_path"] = resolved

            browser = await p.chromium.launch(**launch_args)
            try:
                context = await browser.new_context(**context_kwargs)
                try:
                    await context.set_extra_http_headers(extra_headers)
                    page = await context.new_page()
                    await Stealth().apply_stealth_async(page)
                    await page.goto(url, wait_until="domcontentloaded", timeout=cfg.playwright_timeout_ms)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(cfg.playwright_timeout_ms, 5000))
                    except Exception:
                        pass

                    for _ in range(max(1, cfg.playwright_challenge_wait)):
                        try:
                            title = await page.title()
                        except Exception:
                            await page.wait_for_timeout(1000)
                            continue
                        if not looks_like_challenge_text(title):
                            break
                        await page.wait_for_timeout(1000)

                    try:
                        html = await page.content()
                    except Exception:
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=cfg.playwright_timeout_ms)
                            html = await page.content()
                        except Exception as e:
                            return {"success": False, "url": url, "error": str(e), "via_playwright": True}
                    blocked = looks_like_blocked_text(html)

                    if mode == "html":
                        limited_html, was_truncated = limit_content_length(html)
                        result = {
                            "success": True,
                            "url": url,
                            "via_worker": False,
                            "via_playwright": True,
                            "html": limited_html,
                            "truncated": was_truncated,
                            "blocked": blocked,
                        }
                    elif mode in ("text", "markdown"):
                        result = _extract_and_build_result(html, url, mode, blocked)
                    elif mode in ("meta", "metadata"):
                        metadata = _extract_metadata(html)
                        result = {
                            "success": True,
                            "url": url,
                            "via_worker": False,
                            "via_playwright": True,
                            "blocked": blocked,
                            **metadata,
                        }
                    else:
                        result = {
                            "success": False,
                            "url": url,
                            "error": f"Unsupported mode: {mode}",
                            "via_playwright": True,
                        }
                    return result
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as e:
        return {"success": False, "url": url, "error": str(e), "via_playwright": True}
