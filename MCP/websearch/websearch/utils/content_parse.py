"""Content length limiting, markdown link parsing, URL stripping, and AI tag cleaning."""

from __future__ import annotations

import re
from .config import get_config
from .url_helpers import normalize_url_for_dedup, unwrap_redirect_url


def limit_content_length(content: str) -> tuple[str, bool]:
    cfg = get_config()
    estimated_tokens = len(content) // 4
    if estimated_tokens > cfg.max_token_limit:
        chars_to_keep = cfg.max_token_limit * 4
        return content[:chars_to_keep], True
    return content, False


def clean_ai_tags(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r'<grok:render[^>]*>[\s\S]*?</grok:render>', '', text)
    text = re.sub(r'<[a-z_]+:[^>]+>[\s\S]*?</[a-z_]+:[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def strip_urls(text: str) -> str:
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
    cleaned_lines: list[str] = []
    for line in lines:
        if re.match(r"^\s*[-*]\s*$", line):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_markdown_links(
    content: str, extra_text: str = "",
) -> tuple[list[dict[str, str]], str]:
    content = content or ""
    extra_text = extra_text or ""
    link_source = f"{content}\n{extra_text}" if extra_text else content
    links: list[dict[str, str]] = []
    seen_urls: set[str] = set()

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
        url = unwrap_redirect_url(url)
        return url.strip()

    md_pattern = r'\[([^\]]+)\]\(((?:https?://|//|www\.)[^)\s]+)\)'
    for match in re.finditer(md_pattern, link_source):
        title = match.group(1).strip()
        url = _normalize_candidate(match.group(2))
        if not url.startswith("http"):
            continue
        dedup_key = normalize_url_for_dedup(url) or url
        if dedup_key not in seen_urls:
            seen_urls.add(dedup_key)
            links.append({"title": title, "url": url, "description": ""})

    content_without_md = re.sub(md_pattern, '', link_source)
    url_pattern = r'(?:https?://|//|www\.)[^\s<>\"\'\)\]，。、；：）】}]+'
    for match in re.finditer(url_pattern, content_without_md):
        url = _normalize_candidate(match.group(0))
        if not url.startswith("http") or len(url) <= 10:
            continue
        dedup_key = normalize_url_for_dedup(url) or url
        if dedup_key not in seen_urls:
            seen_urls.add(dedup_key)
            title = url.split('//')[-1].split('/')[0]
            links.append({"title": title, "url": url, "description": ""})

    json_url_pattern = r'"url"\s*:\s*"([^"]+)"'
    for match in re.finditer(json_url_pattern, link_source):
        url = _normalize_candidate(match.group(1))
        if not url.startswith("http") or len(url) <= 10:
            continue
        dedup_key = normalize_url_for_dedup(url) or url
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

    summary = clean_ai_tags(summary)

    return links, summary


def extract_browse_page_links(
    content: str, extra_text: str = "",
) -> list[dict[str, str]]:
    """Extract URLs from Grok-style tool trace lines."""
    source = f"{content}\n{extra_text}" if extra_text else (content or "")
    if not source:
        return []

    links: list[dict[str, str]] = []
    seen: set[str] = set()

    browse_pattern = re.compile(
        r"browse_page\s*\{\s*\"url\"\s*:\s*\"((?:[^\"\\]|\\.)+)\""
        r"(?:\s*,\s*\"instructions\"\s*:\s*\"((?:[^\"\\]|\\.)*)\")?\s*\}",
        flags=re.IGNORECASE,
    )

    def _unescape(value: str) -> str:
        raw = value or ""
        return raw.replace("\\/", "/").replace('\\"', '"')

    for match in browse_pattern.finditer(source):
        raw_url = _unescape(match.group(1).strip())
        instruction = _unescape((match.group(2) or "").strip())
        url = unwrap_redirect_url(raw_url)
        if not url or not url.startswith("http"):
            continue
        key = normalize_url_for_dedup(url) or url
        if key in seen:
            continue
        seen.add(key)
        title = (
            f"browse_page: {instruction[:80].strip()}"
            if instruction
            else url.split("//")[-1].split("/")[0]
        )
        links.append({"title": title, "url": url, "description": ""})

    return links
