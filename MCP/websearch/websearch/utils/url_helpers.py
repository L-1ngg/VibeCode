"""URL normalization, redirect unwrapping, and hostname utilities."""

from __future__ import annotations

import os
import re

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "igshid", "spm", "spm_id_from",
    "from", "from_source", "source", "sourcefrom",
    "share_source", "share_medium", "share_platform", "share_id",
    "share_from", "shareuid", "scene", "platform",
    "ref", "refer", "ref_source", "referrer",
    "vd_source", "_t", "_r", "mpshare",
}

_SITE_QUERY_RE = re.compile(r"(?<!\S)site\s*:\s*([^\s]+)", re.IGNORECASE)

_REDIRECT_PARAM_CANDIDATES = (
    "uddg", "target", "url", "q", "u", "to",
    "dest", "destination", "redir", "redirect",
)

_ZHIHU_ANSWER_RE = re.compile(
    r"zhihu\.com/(?:question/\d+/)?answer/(\d+)", re.IGNORECASE,
)


def normalize_url_for_dedup(url: str) -> str:
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


def unwrap_redirect_url(url: str) -> str:
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


def get_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_site_query(query: str) -> bool:
    return bool(_SITE_QUERY_RE.search(query or ""))


def prefer_playwright_for_url(url: str) -> bool:
    host = get_hostname(url)
    if not host:
        return False
    if host.endswith(("xiaohongshu.com", "xhslink.com")):
        return True
    if host.endswith("zhihu.com"):
        return True
    return False


def extract_zhihu_answer_id(url: str) -> str | None:
    match = _ZHIHU_ANSWER_RE.search(url or "")
    if match:
        return match.group(1)
    return None


def resolve_playwright_executable_path(path: str) -> str | None:
    """Work around occasional Playwright arch/path mismatches."""
    if not path:
        return None
    if os.path.exists(path):
        return path

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
