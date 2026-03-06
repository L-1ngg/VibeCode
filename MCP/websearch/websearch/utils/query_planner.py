from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re


_POLITE_PREFIX_RE = re.compile(
    r"^\s*(?:please|can you|could you|would you|help me|tell me|show me)\s+",
    re.IGNORECASE,
)
_SITE_QUERY_RE = re.compile(r"(?<!\S)site\s*:\s*([^\s]+)", re.IGNORECASE)
_VERSION_RE = re.compile(r"\b(?:v)?\d+(?:\.\d+){0,3}\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b20\d{2}\b")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._+-]{1,}")
_CJK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_SPACE_RE = re.compile(r"\s+")

_ASCII_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "when",
    "where",
    "which",
    "best",
    "latest",
    "today",
    "recent",
    "official",
    "documentation",
    "docs",
    "guide",
    "tutorial",
    "issue",
    "error",
    "problem",
    "how",
    "why",
    "use",
    "using",
    "about",
    "from",
    "into",
}
_CJK_TROUBLESHOOT = ("\u62a5\u9519", "\u9519\u8bef", "\u5f02\u5e38", "\u5931\u8d25", "\u95ee\u9898")
_CJK_COMPARE = ("\u5bf9\u6bd4", "\u533a\u522b", "\u5dee\u5f02")
_CJK_OFFICIAL = ("\u5b98\u65b9", "\u6587\u6863", "\u5b98\u7f51")
_CJK_HOWTO = ("\u5982\u4f55", "\u600e\u4e48", "\u6559\u7a0b", "\u793a\u4f8b", "\u6700\u4f73\u5b9e\u8df5")
_CJK_FRESH = ("\u6700\u65b0", "\u4eca\u5929", "\u6700\u8fd1", "\u8fd1\u671f")


@dataclass(frozen=True)
class QueryRewrite:
    query: str
    reason: str


@dataclass(frozen=True)
class QueryPlan:
    raw_query: str
    normalized_query: str
    intent: str
    is_site_query: bool
    entities: tuple[str, ...]
    source_preferences: tuple[str, ...]
    rewrites: tuple[QueryRewrite, ...]
    constraints: dict[str, str]
    search_budget: int
    fetch_budget: int


def normalize_query(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""
    text = _POLITE_PREFIX_RE.sub("", text)
    text = (
        text.replace("\uFF1F", "?")
        .replace("\uFF0C", " ")
        .replace("\u3002", " ")
        .replace("\u3001", " ")
    )
    text = _SPACE_RE.sub(" ", text)
    return text.strip(" ?")


def plan_query(query: str) -> QueryPlan:
    raw_query = (query or "").strip()
    normalized_query = normalize_query(raw_query) or raw_query
    is_site = bool(_SITE_QUERY_RE.search(raw_query))
    constraints = _extract_constraints(raw_query)
    entities = _extract_entities(raw_query, normalized_query)
    intent = _detect_intent(raw_query, normalized_query, entities, constraints)
    source_preferences = _build_source_preferences(intent, is_site)
    rewrites = _build_rewrites(raw_query, normalized_query, intent, is_site, entities, constraints)
    search_budget, fetch_budget = _build_budgets(intent, is_site)
    return QueryPlan(
        raw_query=raw_query,
        normalized_query=normalized_query,
        intent=intent,
        is_site_query=is_site,
        entities=entities,
        source_preferences=source_preferences,
        rewrites=rewrites,
        constraints=constraints,
        search_budget=search_budget,
        fetch_budget=fetch_budget,
    )


def summarize_query_plan(plan: QueryPlan) -> dict[str, object]:
    return {
        "raw_query": plan.raw_query,
        "normalized_query": plan.normalized_query,
        "intent": plan.intent,
        "is_site_query": plan.is_site_query,
        "entities": list(plan.entities),
        "source_preferences": list(plan.source_preferences),
        "rewrites": [{"query": item.query, "reason": item.reason} for item in plan.rewrites],
        "constraints": dict(plan.constraints),
        "search_budget": plan.search_budget,
        "fetch_budget": plan.fetch_budget,
    }


def build_ai_search_prompt(plan: QueryPlan) -> str:
    rewrites = "\n".join(f"- {item.query}" for item in plan.rewrites[: plan.search_budget])
    preferences = ", ".join(plan.source_preferences) or "general"
    constraints = ", ".join(f"{key}={value}" for key, value in plan.constraints.items()) or "none"
    return (
        "You are a research search assistant. Use high-trust sources, cross-check findings, and avoid fabrication.\n"
        f"Original question: {plan.raw_query}\n"
        f"Normalized question: {plan.normalized_query}\n"
        f"Intent: {plan.intent}\n"
        f"Preferred sources: {preferences}\n"
        f"Constraints: {constraints}\n"
        f"Suggested search queries:\n{rewrites}\n"
        "Output requirements:\n"
        "1) Write the answer in natural language and do not include any URL in the body.\n"
        "2) Append a final section that starts with a single line `SOURCES:` and then list one URL per line, up to 30 lines.\n"
        "3) If sources conflict, explicitly call out the uncertainty in the answer body."
    )


def _extract_constraints(raw_query: str) -> dict[str, str]:
    constraints: dict[str, str] = {}
    site_match = _SITE_QUERY_RE.search(raw_query)
    if site_match:
        constraints["site"] = site_match.group(1)
    version_match = _VERSION_RE.search(raw_query)
    if version_match:
        constraints["version"] = version_match.group(0)
    year_match = _YEAR_RE.search(raw_query)
    if year_match:
        constraints["year"] = year_match.group(0)

    lowered = raw_query.lower()
    if any(word in lowered for word in ("latest", "today", "recent", "news", "release")) or any(
        word in raw_query for word in _CJK_FRESH
    ):
        constraints["freshness"] = "recent"
    if any(word in lowered for word in ("official", "documentation", "docs")) or any(
        word in raw_query for word in _CJK_OFFICIAL
    ):
        constraints["source"] = "official"
    if re.search(r"[\u4e00-\u9fff]", raw_query):
        constraints["language"] = "zh"
    elif re.search(r"[A-Za-z]", raw_query):
        constraints["language"] = "en"
    return constraints


def _extract_entities(raw_query: str, normalized_query: str) -> tuple[str, ...]:
    entities: list[str] = []
    seen: set[str] = set()

    def _push(value: str) -> None:
        text = value.strip()
        key = text.casefold()
        if not text or key in seen:
            return
        seen.add(key)
        entities.append(text)

    for match in _VERSION_RE.finditer(raw_query):
        _push(match.group(0))
    for match in _YEAR_RE.finditer(raw_query):
        _push(match.group(0))
    for match in _ASCII_TOKEN_RE.finditer(raw_query):
        token = match.group(0)
        if token.casefold() in _ASCII_STOPWORDS:
            continue
        _push(token)
    for match in _CJK_TOKEN_RE.finditer(normalized_query):
        token = match.group(0)
        if len(token) >= 2:
            _push(token)
    if not entities and normalized_query:
        _push(normalized_query)
    return tuple(entities[:8])


def _detect_intent(
    raw_query: str,
    normalized_query: str,
    entities: tuple[str, ...],
    constraints: dict[str, str],
) -> str:
    lowered = raw_query.lower()
    if "freshness" in constraints:
        return "latest"
    if any(word in lowered for word in ("error", "exception", "traceback", "failed", "failure", "issue")) or any(
        word in raw_query for word in _CJK_TROUBLESHOOT
    ):
        return "troubleshoot"
    if any(word in lowered for word in ("compare", "vs", "versus", "difference")) or any(
        word in raw_query for word in _CJK_COMPARE
    ):
        return "compare"
    if "source" in constraints or any(word in lowered for word in ("official", "documentation", "docs")) or any(
        word in raw_query for word in _CJK_OFFICIAL
    ):
        return "official_docs"
    if any(word in lowered for word in ("how", "guide", "tutorial", "example", "best practice")) or any(
        word in raw_query for word in _CJK_HOWTO
    ):
        return "howto"
    if len(entities) >= 2 and len(normalized_query) > 24:
        return "howto"
    return "fact"


def _build_source_preferences(intent: str, is_site_query: bool) -> tuple[str, ...]:
    if is_site_query:
        return ("site", "official", "community")
    if intent == "official_docs":
        return ("official", "github", "community")
    if intent == "troubleshoot":
        return ("github", "official", "community")
    if intent == "latest":
        return ("official", "github", "news")
    if intent in {"howto", "compare"}:
        return ("official", "github", "community")
    return ("official", "community", "github")


def _build_rewrites(
    raw_query: str,
    normalized_query: str,
    intent: str,
    is_site_query: bool,
    entities: tuple[str, ...],
    constraints: dict[str, str],
) -> tuple[QueryRewrite, ...]:
    rewrites: list[QueryRewrite] = []
    seen: set[str] = set()
    current_year = str(datetime.now().year)
    base_terms = " ".join(item for item in entities if not item.isdigit()) or normalized_query or raw_query

    def _add(text: str, reason: str) -> None:
        query = re.sub(r"\s+", " ", (text or "").strip())
        key = query.casefold()
        if not query or key in seen:
            return
        seen.add(key)
        rewrites.append(QueryRewrite(query=query, reason=reason))

    _add(raw_query, "original")
    if normalized_query and normalized_query != raw_query:
        _add(normalized_query, "normalized")
    if base_terms and base_terms.casefold() != normalized_query.casefold():
        _add(base_terms, "entity_focus")

    if intent in {"official_docs", "howto", "fact"}:
        _add(f"{base_terms} official documentation", "official_docs")
        _add(f"{base_terms} docs", "docs_short")
    if intent == "troubleshoot":
        _add(f"{base_terms} github issue", "github_issue")
        _add(f"{base_terms} error", "error_focus")
    if intent == "latest":
        _add(f"{base_terms} {constraints.get('year', current_year)}", "freshness_year")
        _add(f"{base_terms} latest release notes", "release_notes")
    if intent == "compare":
        _add(f"{base_terms} comparison", "compare_focus")
    if not is_site_query and intent in {"howto", "troubleshoot", "official_docs"}:
        _add(f"{base_terms} site:github.com", "github_site")

    if is_site_query and "site" in constraints:
        site = constraints["site"]
        compact_terms = " ".join(item for item in entities if item.casefold() != site.casefold()) or normalized_query
        _add(f"site:{site} {compact_terms}", "site_refined")

    return tuple(rewrites[:6])


def _build_budgets(intent: str, is_site_query: bool) -> tuple[int, int]:
    if is_site_query:
        return 2, 4
    if intent in {"latest", "troubleshoot"}:
        return 4, 5
    if intent in {"howto", "official_docs", "compare"}:
        return 4, 5
    return 3, 4


__all__ = [
    "QueryPlan",
    "QueryRewrite",
    "build_ai_search_prompt",
    "normalize_query",
    "plan_query",
    "summarize_query_plan",
]
