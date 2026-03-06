from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..utils.answer_prompt import build_ai_answer_prompt, build_fallback_answer_summary
from ..utils.config import get_config, init_runtime
from ..utils.content_parse import (
    extract_browse_page_links,
    limit_content_length,
    parse_markdown_links,
    strip_urls,
)
from ..utils.evidence_ranker import (
    EvidenceItem,
    FetchedCandidate,
    build_evidence_items,
    build_evidence_pack,
    rerank_evidence_items,
    should_stop_fetching,
    summarize_evidence_items,
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
from ..utils.query_planner import build_ai_search_prompt, plan_query, summarize_query_plan
from ..utils.rerank import rerank_search_results
from ..utils.url_helpers import get_hostname, normalize_url_for_dedup, prefer_playwright_for_url
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
    except Exception as error:
        logger.error("Fetch failed %s: %s", url, error)
        if get_config().playwright_fallback:
            return await _fetch_with_playwright(url, mode="markdown", headers=headers)
        return {"success": False, "url": url, "error": str(error)}

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
    plan = plan_query(query)
    timing: dict[str, int] = {}
    overall_started_at = perf_counter()

    ai_summary = ""
    ai_error = ""
    answer_mode = "browser_results"
    ai_priority_links: list[dict[str, Any]] = []
    ai_links_only: list[dict[str, Any]] = []
    browser_links: list[dict[str, Any]] = []
    browser_diagnostics: dict[str, Any] = {
        "backend": "none",
        "fallback_used": False,
        "brave_results": 0,
        "ddg_results": 0,
        "variants": [],
    }

    async def _ai_search_async() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str]:
        if not _llm_configured():
            return [], [], "", "llm_not_configured"

        def _ai_search() -> tuple[str, str]:
            prompt = build_ai_search_prompt(plan)
            return call_openai_chat_completions(prompt)

        loop = asyncio.get_running_loop()
        try:
            raw_content, raw_reasoning = await loop.run_in_executor(None, _ai_search)
        except Exception as error:
            logger.warning("AI search unavailable, fallback: %s", error)
            return [], [], "", str(error)

        priority_links = [
            {**link, "source": "ai_priority", "query_variant": "ai_planner"}
            for link in extract_browse_page_links(raw_content, extra_text=raw_reasoning)
        ]
        ai_links, summary = parse_markdown_links(raw_content, extra_text=raw_reasoning)
        ai_links = [{**link, "source": "ai", "query_variant": "ai_planner"} for link in ai_links]
        priority_keys = {normalize_url_for_dedup(item.get("url", "")) or item.get("url", "") for item in priority_links}
        ai_links_others = [
            item
            for item in ai_links
            if (normalize_url_for_dedup(item.get("url", "")) or item.get("url", "")) not in priority_keys
        ]
        summary = strip_urls(summary)
        logger.info(
            "AI search done: links=%s browse_page_links=%s",
            len(ai_links),
            len(priority_links),
        )
        return priority_links, ai_links_others, summary, ""

    async def _browser_search_variant_async(search_query: str, reason: str, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        diagnostics: dict[str, Any] = {
            "query": search_query,
            "reason": reason,
            "backend": "none",
            "fallback_used": False,
            "brave_results": 0,
            "ddg_results": 0,
        }

        try:
            results = await _search_brave_core(query=search_query, max_results=limit)
        except Exception as error:
            diagnostics["brave_error"] = str(error)
            results = []

        diagnostics["brave_results"] = len(results)
        if results:
            diagnostics["backend"] = "brave"
            logger.info("Browser search done (Brave), query=%s results=%s", search_query, len(results))
            return [
                {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "description": str(item.get("description") or ""),
                    "source": "browser",
                    "query_variant": search_query,
                    "query_reason": reason,
                }
                for item in results
            ], diagnostics

        diagnostics["fallback_used"] = True
        try:
            fallback = await _search_duckduckgo_core(query=search_query, max_results=limit)
        except Exception as error:
            diagnostics["ddg_error"] = str(error)
            fallback = []
        diagnostics["ddg_results"] = len(fallback)
        if fallback:
            diagnostics["backend"] = "ddg"
        logger.info("Browser search done (DDG), query=%s results=%s", search_query, len(fallback))
        return [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "description": str(item.get("description") or ""),
                "source": "browser",
                "query_variant": search_query,
                "query_reason": reason,
            }
            for item in fallback
        ], diagnostics

    async def _browser_search_async() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        scheduled_rewrites = list(plan.rewrites[: max(1, plan.search_budget)])
        per_query_limit = max(5, min(12, cfg.search_result_limit + 3))
        diagnostics: dict[str, Any] = {
            "backend": "none",
            "fallback_used": False,
            "brave_results": 0,
            "ddg_results": 0,
            "variants": [],
            "scheduled_queries": [item.query for item in scheduled_rewrites],
            "brave_errors": [],
            "ddg_errors": [],
        }

        outcomes = await asyncio.gather(
            *[
                _browser_search_variant_async(item.query, item.reason, per_query_limit)
                for item in scheduled_rewrites
            ],
            return_exceptions=True,
        )

        collected: list[dict[str, Any]] = []
        backends: set[str] = set()
        for item, outcome in zip(scheduled_rewrites, outcomes):
            if isinstance(outcome, Exception):
                diagnostics["variants"].append(
                    {
                        "query": item.query,
                        "reason": item.reason,
                        "backend": "none",
                        "error": str(outcome),
                    }
                )
                continue

            variant_results, variant_diagnostics = outcome
            collected.extend(variant_results)
            diagnostics["fallback_used"] = diagnostics["fallback_used"] or bool(
                variant_diagnostics.get("fallback_used", False)
            )
            diagnostics["brave_results"] += int(variant_diagnostics.get("brave_results", 0) or 0)
            diagnostics["ddg_results"] += int(variant_diagnostics.get("ddg_results", 0) or 0)
            if variant_diagnostics.get("brave_error"):
                diagnostics["brave_errors"].append(str(variant_diagnostics["brave_error"]))
            if variant_diagnostics.get("ddg_error"):
                diagnostics["ddg_errors"].append(str(variant_diagnostics["ddg_error"]))
            backend = str(variant_diagnostics.get("backend") or "none")
            if backend != "none":
                backends.add(backend)
            diagnostics["variants"].append({**variant_diagnostics, "result_count": len(variant_results)})

        if len(backends) == 1:
            diagnostics["backend"] = next(iter(backends))
        elif len(backends) > 1:
            diagnostics["backend"] = "mixed"
        if diagnostics["brave_errors"]:
            diagnostics["brave_error"] = diagnostics["brave_errors"][0]
        if diagnostics["ddg_errors"]:
            diagnostics["ddg_error"] = diagnostics["ddg_errors"][0]
        return collected, diagnostics

    search_started_at = perf_counter()
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
    timing["search"] = _elapsed_ms(search_started_at)

    merged_links = (
        ai_priority_links + browser_links + ai_links_only
        if plan.is_site_query
        else ai_priority_links + ai_links_only + browser_links
    )

    rerank_started_at = perf_counter()
    limited_links, ranked_links = rerank_search_results(
        merged_links,
        plan,
        limit=cfg.search_result_limit,
        max_per_domain=cfg.search_max_per_domain,
    )
    timing["coarse_rerank"] = _elapsed_ms(rerank_started_at)

    fetch_budget = max(0, min(plan.fetch_budget, 5))
    selected_candidates, skipped_candidates = _select_fetch_candidates(limited_links, plan, fetch_budget)

    fetch_started_at = perf_counter()
    fetch_results, early_stop, fetch_failed, fetch_skipped = await _selective_fetch_candidates(plan, selected_candidates)
    timing["fetch"] = _elapsed_ms(fetch_started_at)

    evidence_started_at = perf_counter()
    evidence_items = build_evidence_items(fetch_results, plan)
    evidence_top_items, evidence_ranked_items = rerank_evidence_items(
        evidence_items,
        plan,
        limit=min(3, cfg.search_result_limit),
    )
    evidence_pack = build_evidence_pack(evidence_ranked_items, plan, limit=min(3, cfg.search_result_limit))
    timing["evidence_rerank"] = _elapsed_ms(evidence_started_at)

    answer_started_at = perf_counter()
    if use_ai and evidence_pack.items:
        try:
            prompt = build_ai_answer_prompt(plan, evidence_pack.items)
            loop = asyncio.get_running_loop()
            raw_content, raw_reasoning = await loop.run_in_executor(None, lambda: call_openai_chat_completions(prompt))
            answer_text = strip_urls(raw_content or raw_reasoning)
            if answer_text:
                ai_summary = answer_text
                answer_mode = "llm_evidence_summary"
        except Exception as error:
            ai_error = _merge_error(ai_error, f"answer_summary: {error}")

    if not ai_summary and evidence_pack.items:
        ai_summary = build_fallback_answer_summary(plan, evidence_pack.items)
        answer_mode = "evidence_fallback"
    elif not ai_summary and use_ai and ai_error:
        answer_mode = "llm_failed"
    elif ai_summary and answer_mode != "llm_evidence_summary":
        answer_mode = "llm_search_summary"
    timing["summary"] = _elapsed_ms(answer_started_at)
    timing["total"] = _elapsed_ms(overall_started_at)

    final_links = _compose_final_links(limited_links, evidence_pack.items, cfg.search_result_limit)
    coarse_preview = _summarize_ranked_links(limited_links)

    return {
        "success": True,
        "query": query,
        "links": [{"title": item.get("title", ""), "url": item.get("url", "")} for item in final_links],
        "ai_summary": ai_summary,
        "ai_error": ai_error,
        "diagnostics": {
            "search_backend": browser_diagnostics.get("backend", "none"),
            "browser": browser_diagnostics,
            "is_site_query": plan.is_site_query,
            "llm_enabled": use_ai,
            "answer_mode": answer_mode,
            "query_plan": summarize_query_plan(plan),
            "ranking_preview": coarse_preview,
            "coarse_ranked": coarse_preview,
            "fetch_selected": [_summarize_fetched_candidate(item) for item in selected_candidates],
            "fetch_skipped": skipped_candidates + fetch_skipped,
            "fetch_succeeded": len([item for item in evidence_items if item.fetch_success]),
            "fetch_failed": fetch_failed,
            "early_stop": early_stop,
            "evidence_ranked": summarize_evidence_items(evidence_top_items, limit=5),
            "evidence_dropped": [
                item
                for item in summarize_evidence_items(evidence_ranked_items, limit=10)
                if item.get("url") not in {entry.url for entry in evidence_pack.items}
            ],
            "evidence_confidence": evidence_pack.confidence,
            "timing": timing,
        },
    }


async def _selective_fetch_candidates(
    plan: Any,
    candidates: list[FetchedCandidate],
) -> tuple[list[tuple[FetchedCandidate, dict[str, Any]]], bool, list[dict[str, Any]], list[dict[str, Any]]]:
    if not candidates:
        return [], False, [], []

    results: list[tuple[FetchedCandidate, dict[str, Any]]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    early_stop = False

    first_batch = candidates[: min(2, len(candidates))]
    remaining = candidates[len(first_batch):]

    if first_batch:
        batch_results, batch_failed = await _fetch_candidates_batch(first_batch)
        results.extend(batch_results)
        failed.extend(batch_failed)

    evidence_items = build_evidence_items(results, plan)
    evidence_top_items, _ = rerank_evidence_items(evidence_items, plan, limit=2)
    if remaining and should_stop_fetching(evidence_top_items, plan):
        early_stop = True
        skipped.extend([_summarize_fetched_candidate(item, skipped_reason="early_stop") for item in remaining])
        return results, early_stop, failed, skipped

    for candidate in remaining:
        batch_results, batch_failed = await _fetch_candidates_batch([candidate])
        results.extend(batch_results)
        failed.extend(batch_failed)
        evidence_items = build_evidence_items(results, plan)
        evidence_top_items, _ = rerank_evidence_items(evidence_items, plan, limit=2)
        if should_stop_fetching(evidence_top_items, plan):
            early_stop = True
            tail = remaining[remaining.index(candidate) + 1 :]
            skipped.extend([_summarize_fetched_candidate(item, skipped_reason="early_stop") for item in tail])
            break

    return results, early_stop, failed, skipped


async def _fetch_candidates_batch(
    candidates: list[FetchedCandidate],
) -> tuple[list[tuple[FetchedCandidate, dict[str, Any]]], list[dict[str, Any]]]:
    outcomes = await asyncio.gather(*[_fetch_candidate(candidate) for candidate in candidates], return_exceptions=True)
    results: list[tuple[FetchedCandidate, dict[str, Any]]] = []
    failed: list[dict[str, Any]] = []
    for candidate, outcome in zip(candidates, outcomes):
        if isinstance(outcome, Exception):
            failed.append({"title": candidate.title, "url": candidate.url, "error": str(outcome)})
            results.append((candidate, {"success": False, "url": candidate.url, "error": str(outcome)}))
            continue
        fetch_result = outcome
        if not fetch_result.get("success"):
            failed.append(
                {
                    "title": candidate.title,
                    "url": candidate.url,
                    "error": str(fetch_result.get("error") or "fetch_failed"),
                }
            )
        results.append((candidate, fetch_result))
    return results, failed


async def _fetch_candidate(candidate: FetchedCandidate) -> dict[str, Any]:
    return await fetch(candidate.url)


def _select_fetch_candidates(
    ranked_links: list[dict[str, Any]],
    plan: Any,
    fetch_budget: int,
) -> tuple[list[FetchedCandidate], list[dict[str, Any]]]:
    if fetch_budget <= 0:
        return [], []

    selected: list[FetchedCandidate] = []
    skipped: list[dict[str, Any]] = []
    host_counts: dict[str, int] = {}
    max_per_domain = 2 if plan.is_site_query else 1

    for rank, item in enumerate(ranked_links, start=1):
        url = str(item.get("url") or "")
        host = get_hostname(url)
        if host and host_counts.get(host, 0) >= max_per_domain:
            skipped.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "host": host,
                    "skipped_reason": "domain_limit",
                }
            )
            continue
        selected.append(
            FetchedCandidate(
                title=str(item.get("title") or ""),
                url=url,
                description=str(item.get("description") or ""),
                source=str(item.get("source") or "browser"),
                query_variant=str(item.get("query_variant") or ""),
                query_reason=str(item.get("query_reason") or ""),
                coarse_score=float(item.get("score") or 0.0),
                coarse_rank=rank,
            )
        )
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
        if len(selected) >= fetch_budget:
            break

    return selected, skipped


def _compose_final_links(
    ranked_links: list[dict[str, Any]],
    evidence_items: tuple[EvidenceItem, ...],
    limit: int,
) -> list[dict[str, Any]]:
    evidence_urls = [item.url for item in evidence_items]
    ranked_by_url = {str(item.get("url") or ""): item for item in ranked_links}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in evidence_urls:
        if url in ranked_by_url and url not in seen:
            merged.append(ranked_by_url[url])
            seen.add(url)
    for item in ranked_links:
        url = str(item.get("url") or "")
        if url and url not in seen:
            merged.append(item)
            seen.add(url)
        if len(merged) >= limit:
            break
    return merged[:limit]


def _summarize_ranked_links(ranked_links: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "score": item.get("score", 0),
            "source": item.get("source", "browser"),
            "query_variant": item.get("query_variant", ""),
        }
        for item in ranked_links[:limit]
    ]


def _summarize_fetched_candidate(candidate: FetchedCandidate, *, skipped_reason: str | None = None) -> dict[str, Any]:
    payload = {
        "title": candidate.title,
        "url": candidate.url,
        "coarse_rank": candidate.coarse_rank,
        "coarse_score": candidate.coarse_score,
        "query_variant": candidate.query_variant,
        "query_reason": candidate.query_reason,
    }
    if skipped_reason:
        payload["skipped_reason"] = skipped_reason
    return payload


def _merge_error(current: str, incoming: str) -> str:
    if not current:
        return incoming
    if incoming in current:
        return current
    return f"{current}; {incoming}"


def _elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


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
