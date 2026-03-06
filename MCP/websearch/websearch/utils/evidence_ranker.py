from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from .query_planner import QueryPlan
from .url_helpers import get_hostname

_TOKEN_RE = re.compile(r"[A-Za-z0-9._+-]+|[\u4e00-\u9fff]{2,}")
_YEAR_RE = re.compile(r"\b20\d{2}\b")
_STEP_HINTS = ("step", "steps", "install", "configure", "setup", "example", "usage")
_FIX_HINTS = ("fix", "workaround", "solution", "resolve", "resolved", "cause", "error", "exception")
_RELEASE_HINTS = ("release", "changelog", "announcement", "version", "latest", "updated")
_COMMUNITY_HINTS = ("stackoverflow.com", "stackexchange.com", "forum", "discuss", "linux.do")
_BLOG_HINTS = ("blog", "medium.com", "substack.com")
_OFFICIAL_HINTS = (
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


@dataclass(frozen=True)
class FetchedCandidate:
    title: str
    url: str
    description: str
    source: str
    query_variant: str
    query_reason: str
    coarse_score: float
    coarse_rank: int


@dataclass(frozen=True)
class EvidenceSnippet:
    text: str
    kind: str
    match_terms: tuple[str, ...]
    local_score: float


@dataclass(frozen=True)
class EvidenceItem:
    title: str
    url: str
    host: str
    source: str
    query_variant: str
    query_reason: str
    coarse_score: float
    coarse_rank: int
    snippets: tuple[EvidenceSnippet, ...]
    fetch_success: bool
    blocked: bool
    quality_score: float
    score: float
    score_breakdown: dict[str, float]
    reason_tags: tuple[str, ...]


@dataclass(frozen=True)
class EvidencePack:
    items: tuple[EvidenceItem, ...]
    top_sources: tuple[str, ...]
    coverage_terms: tuple[str, ...]
    confidence: float


def build_evidence_items(
    fetched_results: Iterable[tuple[FetchedCandidate, dict[str, Any]]],
    plan: QueryPlan,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for candidate, fetch_result in fetched_results:
        fetch_payload = fetch_result if isinstance(fetch_result, dict) else {}
        markdown = str(fetch_payload.get("markdown") or "")
        snippets = _extract_snippets(markdown, plan)
        breakdown = _build_score_breakdown(candidate, fetch_payload, snippets, plan)
        score = round(sum(breakdown.values()), 3)
        reason_tags = _build_reason_tags(candidate, fetch_payload, snippets, plan, breakdown)
        items.append(
            EvidenceItem(
                title=candidate.title,
                url=candidate.url,
                host=get_hostname(candidate.url),
                source=candidate.source,
                query_variant=candidate.query_variant,
                query_reason=candidate.query_reason,
                coarse_score=candidate.coarse_score,
                coarse_rank=candidate.coarse_rank,
                snippets=snippets,
                fetch_success=bool(fetch_payload.get("success")),
                blocked=bool(fetch_payload.get("blocked")),
                quality_score=float(fetch_payload.get("quality_score") or 0.0),
                score=score,
                score_breakdown=breakdown,
                reason_tags=reason_tags,
            )
        )
    return items


def rerank_evidence_items(
    evidence_items: Iterable[EvidenceItem],
    plan: QueryPlan,
    *,
    limit: int,
) -> tuple[list[EvidenceItem], list[EvidenceItem]]:
    ranked = sorted(
        evidence_items,
        key=lambda item: (item.score, item.quality_score, -item.coarse_rank),
        reverse=True,
    )
    if limit <= 0:
        return [], ranked

    selected: list[EvidenceItem] = []
    host_counts: dict[str, int] = {}
    max_per_domain = 2 if plan.is_site_query else 1
    for item in ranked:
        if not item.fetch_success or not item.snippets:
            continue
        if item.host and host_counts.get(item.host, 0) >= max_per_domain:
            continue
        selected.append(item)
        if item.host:
            host_counts[item.host] = host_counts.get(item.host, 0) + 1
        if len(selected) >= limit:
            break
    return selected, ranked


def build_evidence_pack(
    evidence_items: Iterable[EvidenceItem],
    plan: QueryPlan,
    *,
    limit: int,
) -> EvidencePack:
    top_items, ranked_items = rerank_evidence_items(evidence_items, plan, limit=limit)
    coverage_terms: set[str] = set()
    sources: list[str] = []
    for item in top_items:
        sources.append(item.url)
        for snippet in item.snippets:
            coverage_terms.update(snippet.match_terms)
    confidence = 0.0
    if ranked_items:
        score_total = sum(max(item.score, 0.0) for item in top_items)
        confidence = round(min(1.0, score_total / max(10.0, len(top_items) * 8.0)), 3)
    return EvidencePack(
        items=tuple(top_items),
        top_sources=tuple(sources),
        coverage_terms=tuple(sorted(coverage_terms)),
        confidence=confidence,
    )


def should_stop_fetching(evidence_items: Iterable[EvidenceItem], plan: QueryPlan) -> bool:
    items = [item for item in evidence_items if item.fetch_success and item.snippets and not item.blocked]
    if not items:
        return False
    strong_items = [item for item in items if item.score >= 7.2]
    distinct_hosts = {item.host for item in strong_items if item.host}
    covered_terms: set[str] = set()
    for item in strong_items[:3]:
        for snippet in item.snippets:
            covered_terms.update(snippet.match_terms)

    if plan.intent in {"fact", "official_docs"}:
        return bool(strong_items and strong_items[0].score >= 8.4)
    if len(strong_items) >= 2 and len(distinct_hosts) >= 2 and len(covered_terms) >= min(3, len(_collect_terms(plan))):
        return True
    if plan.intent == "troubleshoot" and strong_items and any("troubleshoot" in item.reason_tags for item in strong_items[:2]):
        return True
    return False


def summarize_evidence_items(evidence_items: Iterable[EvidenceItem], *, limit: int | None = None) -> list[dict[str, Any]]:
    items = list(evidence_items)
    if limit is not None:
        items = items[:limit]
    return [
        {
            "title": item.title,
            "url": item.url,
            "host": item.host,
            "score": item.score,
            "quality_score": item.quality_score,
            "coarse_rank": item.coarse_rank,
            "snippet_count": len(item.snippets),
            "source": item.source,
            "query_variant": item.query_variant,
            "reason_tags": list(item.reason_tags),
            "score_breakdown": dict(item.score_breakdown),
            "fetch_success": item.fetch_success,
            "blocked": item.blocked,
        }
        for item in items
    ]


def _extract_snippets(markdown: str, plan: QueryPlan) -> tuple[EvidenceSnippet, ...]:
    if not markdown:
        return ()

    blocks = _split_blocks(markdown)
    if not blocks:
        return ()

    query_terms = _collect_terms(plan)
    snippets: list[EvidenceSnippet] = []
    seen: set[str] = set()

    def _add_snippet(text: str, kind: str) -> None:
        snippet = text.strip()
        minimum_length = 20 if kind in {"steps", "error_fix", "release_note"} else 40
        if len(snippet) < minimum_length:
            return
        key = snippet.casefold()
        if key in seen:
            return
        seen.add(key)
        match_terms = tuple(sorted(term for term in query_terms if term in key))
        local_score = round(min(4.0, 0.8 + len(match_terms) * 0.9 + (0.5 if kind != "summary" else 0.0)), 3)
        snippets.append(EvidenceSnippet(text=snippet[:500], kind=kind, match_terms=match_terms, local_score=local_score))

    _add_snippet(blocks[0], "summary")

    for block in blocks[1:]:
        lowered = block.casefold()
        if any(term in lowered for term in query_terms):
            kind = "matched_paragraph"
            if any(hint in lowered for hint in _FIX_HINTS):
                kind = "error_fix"
            elif any(hint in lowered for hint in _STEP_HINTS):
                kind = "steps"
            elif any(hint in lowered for hint in _RELEASE_HINTS) or _YEAR_RE.search(block):
                kind = "release_note"
            _add_snippet(block, kind)
        if len(snippets) >= 4:
            break

    if len(snippets) < 3:
        for block in blocks:
            lowered = block.casefold()
            if any(hint in lowered for hint in _STEP_HINTS):
                _add_snippet(block, "steps")
            elif any(hint in lowered for hint in _RELEASE_HINTS):
                _add_snippet(block, "release_note")
            if len(snippets) >= 4:
                break

    return tuple(snippets[:4])


def _build_score_breakdown(
    candidate: FetchedCandidate,
    fetch_payload: dict[str, Any],
    snippets: tuple[EvidenceSnippet, ...],
    plan: QueryPlan,
) -> dict[str, float]:
    markdown = str(fetch_payload.get("markdown") or "")
    host = get_hostname(candidate.url)
    query_terms = _collect_terms(plan)
    matched_terms = {term for snippet in snippets for term in snippet.match_terms}

    query_coverage = min(4.0, len(matched_terms) * 0.9 + len(snippets) * 0.35)
    source_trust = _score_source_trust(host, plan)
    quality_score = float(fetch_payload.get("quality_score") or 0.0)
    quality = min(3.0, max(0.0, quality_score / 35.0))
    if fetch_payload.get("blocked"):
        quality -= 1.6
    if not fetch_payload.get("success"):
        quality -= 2.0
    freshness = _score_freshness(candidate, markdown, plan)
    answerability = _score_answerability(snippets, markdown, plan)
    diversity = 0.6 if host else 0.0
    coarse_alignment = min(1.2, max(0.0, candidate.coarse_score / 8.0))

    if not matched_terms and query_terms:
        query_coverage = min(query_coverage, 0.8)
    if not snippets:
        answerability = 0.0
        diversity = 0.0

    return {
        "query_coverage": round(query_coverage, 3),
        "source_trust": round(source_trust, 3),
        "content_quality": round(quality, 3),
        "freshness": round(freshness, 3),
        "answerability": round(answerability, 3),
        "diversity": round(diversity, 3),
        "coarse_alignment": round(coarse_alignment, 3),
    }


def _build_reason_tags(
    candidate: FetchedCandidate,
    fetch_payload: dict[str, Any],
    snippets: tuple[EvidenceSnippet, ...],
    plan: QueryPlan,
    breakdown: dict[str, float],
) -> tuple[str, ...]:
    tags: list[str] = []
    host = get_hostname(candidate.url)
    if _looks_official(host):
        tags.append("official")
    if "github.com" in host:
        tags.append("github")
    if any(part in host for part in _COMMUNITY_HINTS):
        tags.append("community")
    if plan.intent == "troubleshoot" and any(snippet.kind == "error_fix" for snippet in snippets):
        tags.append("troubleshoot")
    if plan.intent == "latest" and breakdown.get("freshness", 0.0) >= 1.4:
        tags.append("fresh")
    if fetch_payload.get("blocked"):
        tags.append("blocked")
    if fetch_payload.get("success"):
        tags.append("fetched")
    return tuple(tags)


def _score_source_trust(host: str, plan: QueryPlan) -> float:
    if not host:
        return 0.0
    score = 0.0
    if _looks_official(host):
        score += 3.2
    elif "github.com" in host:
        score += 2.2
    elif any(part in host for part in _COMMUNITY_HINTS):
        score += 1.4
    elif any(part in host for part in _BLOG_HINTS):
        score += 0.8

    if plan.intent == "official_docs" and _looks_official(host):
        score += 1.4
    if plan.intent == "troubleshoot" and ("github.com" in host or any(part in host for part in _COMMUNITY_HINTS)):
        score += 1.0
    if plan.intent == "latest" and any(part in host for part in ("github.com", "blog", "news")):
        score += 0.7
    if plan.constraints.get("site") and plan.constraints["site"] in host:
        score += 1.1
    return min(score, 4.6)


def _score_freshness(candidate: FetchedCandidate, markdown: str, plan: QueryPlan) -> float:
    if plan.intent != "latest":
        return 0.2 if _YEAR_RE.search(candidate.title) else 0.0
    years = [int(match.group(0)) for match in _YEAR_RE.finditer(f"{candidate.title} {candidate.description} {candidate.url} {markdown[:1200]}")]
    if not years:
        return 0.0
    target_year = int(plan.constraints.get("year") or __import__("datetime").datetime.now().year)
    newest = max(years)
    if newest >= target_year:
        return 2.2
    if newest == target_year - 1:
        return 1.2
    return 0.4


def _score_answerability(snippets: tuple[EvidenceSnippet, ...], markdown: str, plan: QueryPlan) -> float:
    if not snippets:
        return 0.0
    lowered = markdown.casefold()
    score = min(2.5, len(snippets) * 0.45)
    if plan.intent == "howto" and any(snippet.kind == "steps" for snippet in snippets):
        score += 1.1
    if plan.intent == "troubleshoot" and any(snippet.kind == "error_fix" for snippet in snippets):
        score += 1.2
    if plan.intent == "latest" and any(snippet.kind == "release_note" for snippet in snippets):
        score += 0.9
    if any(hint in lowered for hint in _STEP_HINTS):
        score += 0.4
    return min(score, 3.4)


def _collect_terms(plan: QueryPlan) -> set[str]:
    terms: set[str] = set()
    for source in [plan.normalized_query, *plan.entities]:
        lowered = source.casefold()
        for match in _TOKEN_RE.finditer(lowered):
            token = match.group(0).strip("._-")
            if len(token) < 2:
                continue
            terms.add(token)
    return terms


def _split_blocks(markdown: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\n\s*\n+", markdown or "")]
    cleaned: list[str] = []
    for part in parts:
        text = re.sub(r"\s+", " ", part).strip(" -#*")
        if len(text) >= 20:
            cleaned.append(text)
    return cleaned


def _looks_official(host: str) -> bool:
    return any(part in host for part in _OFFICIAL_HINTS)


__all__ = [
    "EvidenceItem",
    "EvidencePack",
    "EvidenceSnippet",
    "FetchedCandidate",
    "build_evidence_items",
    "build_evidence_pack",
    "rerank_evidence_items",
    "should_stop_fetching",
    "summarize_evidence_items",
]





