from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..utils.config import get_config, init_runtime
from ..utils.content_parse import (
    extract_browse_page_links,
    limit_content_length,
    parse_markdown_links,
    strip_urls,
)
from ..utils.extraction import (
    _build_degraded_markdown,
    _clean_extracted_markdown,
    _extract_best_content,
    _score_content,
)
from ..utils.html_detect import looks_like_blocked_text
from ..utils.openai_client import call_openai_chat_completions
from ..utils.proxy import get_target_url
from ..utils.url_helpers import (
    get_hostname,
    is_site_query,
    normalize_url_for_dedup,
    prefer_playwright_for_url,
    unwrap_redirect_url,
)
from .fetch_search_core import (
    _curl_get_with_retries,
    _fetch_discourse_topic_content,
    _fetch_with_playwright,
    _fetch_zhihu_answer_content,
    _search_brave_core,
    _search_duckduckgo_core,
)

logger = logging.getLogger(__name__)
mcp = FastMCP("websearch")


def _llm_configured() -> bool:
    return get_config().llm_configured


@mcp.tool()
async def fetch(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        cfg = get_config()

        zhihu_result = _fetch_zhihu_answer_content(url, mode="markdown")
        if zhihu_result:
            return zhihu_result

        discourse_result = _fetch_discourse_topic_content(url)
        if discourse_result:
            return discourse_result

        if prefer_playwright_for_url(url) and cfg.playwright_fallback:
            return {"success": False, "url": url, "needs_playwright": True}

        target_url = get_target_url(url)
        response = _curl_get_with_retries(
            target_url,
            headers=headers,
            timeout_s=cfg.fetch_timeout_s,
            retries=2,
        )

        raw_html = response.text or ""
        blocked = looks_like_blocked_text(raw_html)
        if blocked and cfg.playwright_fallback:
            return {
                "success": False,
                "url": url,
                "needs_playwright": True,
                "via_worker": bool(cfg.cf_worker_url),
                "status_code": response.status_code,
            }
        extracted = _extract_best_content(raw_html, url=url, output_format="markdown")
        if blocked and extracted.get("quality_score", 0) < 65:
            degraded = _build_degraded_markdown(raw_html) or ""
            degraded = _clean_extracted_markdown(degraded)
            metrics = _score_content(degraded)
            extracted = {
                "content": degraded,
                "extractor": "meta:blocked",
                "degraded": True,
                **metrics,
            }
        limited_md, was_truncated = limit_content_length(extracted.get("content", ""))

        return {
            "success": True,
            "url": url,
            "via_worker": bool(cfg.cf_worker_url),
            "via_playwright": False,
            "status_code": response.status_code,
            "markdown": limited_md,
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

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.error("Fetch failed %s: %s", url, e)
        if get_config().playwright_fallback:
            return await _fetch_with_playwright(url, mode="markdown", headers=headers)
        return {"success": False, "url": url, "error": str(e)}

    if result.get("needs_playwright") and get_config().playwright_fallback:
        return await _fetch_with_playwright(url, mode="markdown", headers=headers)

    if looks_like_blocked_text(result.get("markdown", "")) and get_config().playwright_fallback:
        pw_result = await _fetch_with_playwright(url, mode="markdown", headers=headers)
        if pw_result.get("success"):
            return pw_result
        result["blocked"] = True
        result["playwright_error"] = pw_result.get("error", "Playwright fallback failed")

    return result


@mcp.tool()
async def web_search(query: str) -> dict[str, Any]:
    logger.info("Search request: query='%s'", query)

    cfg = get_config()
    is_site = is_site_query(query)

    ai_summary = ""
    ai_error = ""
    ai_priority_links: list[dict[str, str]] = []
    ai_links_only: list[dict[str, str]] = []
    browser_links: list[dict[str, str]] = []
    browser_diagnostics: dict[str, Any] = {
        "backend": "none",
        "fallback_used": False,
        "brave_results": 0,
        "ddg_results": 0,
    }

    async def _ai_search_async() -> tuple[list[dict[str, str]], list[dict[str, str]], str, str]:
        if not _llm_configured():
            return [], [], "", "llm_not_configured"

        def _ai_search() -> tuple[str, str]:
            prompt = f"""你是一个研究型搜索助手。请通过联网检索与交叉验证，给出高质量、细节充分的回答，避免编造。
输出要求：
1) 正文：自然语言写作，不要输出任何 URL/链接（包括 http/https/www 开头内容），也不要出现“参考来源/References/Sources”等段落标题。
2) 末尾追加一段 SOURCES（必须以单独一行 'SOURCES:' 开头），其后每行一个你参考过的来源 URL（最多 30 条）。
用户问题：{query}"""
            return call_openai_chat_completions(prompt)

        loop = asyncio.get_running_loop()
        try:
            raw_content, raw_reasoning = await loop.run_in_executor(None, _ai_search)
        except Exception as e:
            logger.warning("AI search unavailable, fallback: %s", e)
            return [], [], "", str(e)

        priority_links = extract_browse_page_links(raw_content, extra_text=raw_reasoning)
        ai_links, summary = parse_markdown_links(raw_content, extra_text=raw_reasoning)
        priority_keys = {normalize_url_for_dedup(l.get("url", "")) or l.get("url", "") for l in priority_links}
        ai_links_others = [
            link
            for link in ai_links
            if ((normalize_url_for_dedup(link.get("url", "")) or link.get("url", "")) not in priority_keys)
        ]
        summary = strip_urls(summary)
        logger.info(
            "AI search done: links=%s browse_page_links=%s",
            len(ai_links),
            len(priority_links),
        )
        return priority_links, ai_links_others, summary, ""

    async def _browser_search_async() -> tuple[list[dict[str, str]], dict[str, Any]]:
        internal_limit = max(cfg.search_result_limit * 2, 20)
        diagnostics: dict[str, Any] = {
            "backend": "none",
            "fallback_used": False,
            "brave_results": 0,
            "ddg_results": 0,
        }

        try:
            results = await _search_brave_core(query=query, max_results=internal_limit)
        except Exception as e:
            diagnostics["brave_error"] = str(e)
            results = []

        diagnostics["brave_results"] = len(results)
        if results:
            diagnostics["backend"] = "brave"
            logger.info("Browser search done (Brave), results=%s", len(results))
            return results, diagnostics

        diagnostics["fallback_used"] = True
        fallback = await _search_duckduckgo_core(query=query, max_results=internal_limit)
        diagnostics["ddg_results"] = len(fallback)
        if fallback:
            diagnostics["backend"] = "ddg"
        logger.info("Browser search done (DDG), results=%s", len(fallback))
        return fallback, diagnostics

    use_ai = _llm_configured()
    browser_task = asyncio.create_task(_browser_search_async())
    if use_ai:
        ai_task = asyncio.create_task(_ai_search_async())
        ai_result, browser_result = await asyncio.gather(ai_task, browser_task, return_exceptions=True)

        if isinstance(ai_result, Exception):
            logger.warning("AI search failed, degrade to browser only: %s", ai_result)
            ai_error = str(ai_result)
        else:
            ai_priority_links, ai_links_only, ai_summary, ai_error = ai_result

        if isinstance(browser_result, Exception):
            logger.error("Browser search failed: %s", browser_result)
            browser_diagnostics["browser_error"] = str(browser_result)
        else:
            browser_links, browser_diagnostics = browser_result
    else:
        browser_links, browser_diagnostics = await browser_task

    merged_links = (
        ai_priority_links + browser_links + ai_links_only
        if is_site
        else ai_priority_links + ai_links_only + browser_links
    )

    seen_urls: set[str] = set()
    unique_links: list[dict[str, str]] = []
    for link in merged_links:
        if not isinstance(link, dict):
            continue
        raw_url = link.get("url", "")
        url = unwrap_redirect_url(raw_url)
        if not url or not url.startswith("http"):
            continue
        dedup_key = normalize_url_for_dedup(url) or url
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)
        unique_links.append(
            {
                "title": str(link.get("title") or ""),
                "url": url,
            }
        )

    limit = cfg.search_result_limit
    max_per_domain = cfg.search_max_per_domain
    if max_per_domain < 0:
        max_per_domain = 0
    if is_site:
        max_per_domain = 0

    domain_counts: dict[str, int] = {}
    limited_links: list[dict[str, str]] = []
    for link in unique_links:
        url = link.get("url", "")
        host = get_hostname(url) if url else ""
        if max_per_domain > 0 and host:
            if domain_counts.get(host, 0) >= max_per_domain:
                continue
        limited_links.append(link)
        if host:
            domain_counts[host] = domain_counts.get(host, 0) + 1
        if len(limited_links) >= limit:
            break

    return {
        "success": True,
        "query": query,
        "links": limited_links,
        "ai_summary": ai_summary,
        "ai_error": ai_error,
        "diagnostics": {
            "search_backend": browser_diagnostics.get("backend", "none"),
            "browser": browser_diagnostics,
            "is_site_query": is_site,
            "llm_enabled": use_ai,
        },
    }


def main() -> None:
    cfg = init_runtime()
    logger.info("WebSearch MCP Server starting...")
    if cfg.cf_worker_url:
        logger.info("Cloudflare Worker enabled: %s", cfg.cf_worker_url)
    if cfg.proxy:
        logger.info("Proxy enabled: %s", cfg.proxy)
    if cfg.llm_configured:
        logger.info("AI search enabled, model=%s", cfg.openai_model)
    logger.info("Waiting for MCP client connection...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
