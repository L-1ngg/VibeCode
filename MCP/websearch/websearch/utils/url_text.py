"""Backward-compatible re-export shim.

All public logic has moved to focused modules:
  url_helpers, proxy, openai_client, html_detect, content_parse
"""

from __future__ import annotations

# Re-exports with legacy underscore-prefixed names
from .content_parse import (
    clean_ai_tags as _clean_ai_tags,
    extract_browse_page_links as _extract_browse_page_links,
    limit_content_length as _limit_content_length,
    parse_markdown_links as _parse_markdown_links,
    strip_urls as _strip_urls,
)
from .html_detect import (
    html_to_text as _html_to_text,
    looks_like_blocked_text as _looks_like_blocked_text,
    looks_like_challenge_text as _looks_like_challenge_text,
)
from .openai_client import (
    call_openai_chat_completions as _call_openai_chat_completions,
)
from .proxy import (
    get_proxies as _get_proxies,
    get_target_url as _get_target_url,
)
from .url_helpers import (
    extract_zhihu_answer_id as _extract_zhihu_answer_id,
    get_hostname as _get_hostname,
    is_site_query as _is_site_query,
    normalize_url_for_dedup as _normalize_url_for_dedup,
    prefer_playwright_for_url as _prefer_playwright_for_url,
    resolve_playwright_executable_path as _resolve_playwright_executable_path,
    unwrap_redirect_url as _unwrap_redirect_url,
)

__all__ = [
    "_call_openai_chat_completions",
    "_clean_ai_tags",
    "_extract_browse_page_links",
    "_extract_zhihu_answer_id",
    "_get_hostname",
    "_get_proxies",
    "_get_target_url",
    "_html_to_text",
    "_is_site_query",
    "_limit_content_length",
    "_looks_like_blocked_text",
    "_looks_like_challenge_text",
    "_normalize_url_for_dedup",
    "_parse_markdown_links",
    "_prefer_playwright_for_url",
    "_resolve_playwright_executable_path",
    "_strip_urls",
    "_unwrap_redirect_url",
]
