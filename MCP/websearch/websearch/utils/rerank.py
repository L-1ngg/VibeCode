from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from .query_planner import QueryPlan
from .url_helpers import get_hostname, normalize_url_for_dedup, unwrap_redirect_url


_TOKEN_RE = re.compile(r"[A-Za-z0-9._+-]+|[\u4e00-\u9fff]{2,}")
_YEAR_RE = re.compile(r"\b20\d{2}\b")


def rerank_search_results(
    candidates: Iterable[dict[str, Any]],
    plan: QueryPlan,
    *,
    limit: int,
    max_per_domain: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    query_terms = _collect_query_terms(plan)
    ranked: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        raw_url = str(candidate.get("url") or "")
        url = unwrap_redirect_url(raw_url)
        if not url or not url.startswith("http"):
            continue
        dedup_key = normalize_url_for_dedup(url) or url
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)

        item = dict(candidate)
        item["url"] = url
        item["title"] = str(candidate.get("title") or "")
        item["description"] = str(candidate.get("description") or "")
        item["score"] = _score_candidate(item, plan, query_terms, position)
        ranked.append(item)

    ranked.sort(key=lambda item: (item.get("score", 0.0), item.get("title", "")), reverse=True)

    if plan.is_site_query:
        max_per_domain = 0
    if max_per_domain < 0:
        max_per_domain = 0

    domain_counts: dict[str, int] = {}
    limited: list[dict[str, Any]] = []
    for item in ranked:
        host = get_hostname(item.get("url", ""))
        if max_per_domain > 0 and host and domain_counts.get(host, 0) >= max_per_domain:
            continue
        limited.append(item)
        if host:
            domain_counts[host] = domain_counts.get(host, 0) + 1
        if len(limited) >= limit:
            break
    return limited, ranked


def _score_candidate(candidate: dict[str, Any], plan: QueryPlan, query_terms: set[str], position: int) -> float:
    title = str(candidate.get("title") or "")
    description = str(candidate.get("description") or "")
    url = str(candidate.get("url") or "")
    source = str(candidate.get("source") or "browser")
    text_blob = f"{title} {description} {url}".casefold()
    title_blob = title.casefold()
    score = 0.0

    for entity in plan.entities:
        entity_key = entity.casefold()
        if entity_key and entity_key in title_blob:
            score += 3.0
        elif entity_key and entity_key in text_blob:
            score += 1.8

    for term in query_terms:
        if term in title_blob:
            score += 1.4
        elif term in text_blob:
            score += 0.65

    score += _score_domain(url, plan)
    score += _score_origin(source)
    score += max(0.0, 1.2 - (position * 0.08))

    if plan.intent == "latest":
        score += _score_freshness(title, description, url, plan)

    if plan.intent == "official_docs" and _looks_official(url):
        score += 2.8
    if plan.intent == "troubleshoot" and "issue" in text_blob:
        score += 1.6
    return score


def _collect_query_terms(plan: QueryPlan) -> set[str]:
    terms: set[str] = set()
    for source in [plan.normalized_query, *plan.entities]:
        for match in _TOKEN_RE.finditer(source.casefold()):
            token = match.group(0).strip("._-")
            if len(token) < 2:
                continue
            terms.add(token)
    return terms


def _score_domain(url: str, plan: QueryPlan) -> float:
    host = get_hostname(url)
    score = 0.0
    if not host:
        return score
    if _looks_official(url):
        score += 2.4
    if "github.com" in host:
        score += 1.9
    if any(part in host for part in ("stackoverflow.com", "stackexchange.com", "linux.do", "discuss", "forum")):
        score += 1.2
    if any(part in host for part in ("medium.com", "blog", "substack.com")):
        score += 0.4

    for preference in plan.source_preferences:
        if preference == "official" and _looks_official(url):
            score += 1.8
        elif preference == "github" and "github.com" in host:
            score += 1.4
        elif preference == "community" and any(
            part in host for part in ("stackoverflow.com", "stackexchange.com", "forum", "discuss", "linux.do")
        ):
            score += 1.0
        elif preference == "news" and any(part in host for part in ("news", "blog", "release", "substack.com")):
            score += 0.8
        elif preference == "site" and plan.constraints.get("site") and plan.constraints["site"] in host:
            score += 2.2
    return score


def _looks_official(url: str) -> bool:
    host = get_hostname(url)
    if not host:
        return False
    return any(
        part in host
        for part in (
            "docs.",
            "developer.",
            "developers.",
            "readthedocs.io",
            "python.org",
            "mozilla.org",
            "microsoft.com",
            "openai.com",
            "pypi.org",
            "npmjs.com",
        )
    )


def _score_origin(source: str) -> float:
    if source == "ai_priority":
        return 2.8
    if source == "ai":
        return 1.1
    return 0.0


def _score_freshness(title: str, description: str, url: str, plan: QueryPlan) -> float:
    combined = f"{title} {description} {url}"
    years = [int(match.group(0)) for match in _YEAR_RE.finditer(combined)]
    if not years:
        return 0.0
    newest = max(years)
    current_year = int(plan.constraints.get("year") or datetime_year())
    if newest >= current_year:
        return 2.2
    if newest == current_year - 1:
        return 1.2
    return 0.4


def datetime_year() -> int:
    from datetime import datetime

    return datetime.now().year


__all__ = ["rerank_search_results"]
