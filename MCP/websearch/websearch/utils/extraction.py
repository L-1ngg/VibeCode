from __future__ import annotations

import html as html_lib
import logging
import os
import re
from typing import Any

from bs4 import BeautifulSoup

from .config import get_config
from .content_parse import limit_content_length
from .html_detect import html_to_text
from .noise_rules import load_noise_rules
from .url_helpers import get_hostname

logger = logging.getLogger(__name__)

_INTERNAL_TUNING: dict[str, dict[str, Any]] = {
    "quality": {
        "adapter_min_quality": 10,
        "general_min_quality": 30,
        "bonus_adapter": 15,
        "bonus_precision": 10,
        "bonus_recall": 9,
        "bonus_fast": 8,
        "bonus_baseline": 6,
        "early_stop_enabled": True,
        "early_stop_quality": 80,
        "early_stop_chars": 900,
    },
    "balanced": {
        "adapter_min_quality": 8,
        "general_min_quality": 25,
        "bonus_adapter": 13,
        "bonus_precision": 9,
        "bonus_recall": 8,
        "bonus_fast": 8,
        "bonus_baseline": 5,
        "early_stop_enabled": True,
        "early_stop_quality": 72,
        "early_stop_chars": 700,
    },
    "speed": {
        "adapter_min_quality": 6,
        "general_min_quality": 18,
        "bonus_adapter": 10,
        "bonus_precision": 8,
        "bonus_recall": 7,
        "bonus_fast": 9,
        "bonus_baseline": 4,
        "early_stop_enabled": False,
        "early_stop_quality": 65,
        "early_stop_chars": 600,
    },
}


def _is_noise_line(line: str, *, _rules=None) -> bool:
    if not line:
        return False
    stripped = line.strip()
    if not stripped:
        return False

    regex_rules, substring_rules = _rules if _rules is not None else load_noise_rules()
    for pattern in regex_rules:
        if pattern.match(stripped):
            return True

    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", stripped.lower())
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", compact)
    if len(compact) <= 40:
        for needle in substring_rules:
            if needle and needle in compact:
                return True
    return False


def _clean_extracted_text(text: str) -> str:
    if not text:
        return ""
    rules = load_noise_rules()
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _is_noise_line(line, _rules=rules):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _clean_extracted_markdown(markdown: str) -> str:
    if not markdown:
        return ""
    rules = load_noise_rules()
    lines: list[str] = []
    in_code_block = False
    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            lines.append(line)
            continue
        if in_code_block:
            lines.append(line)
            continue
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("#"):
            line = re.sub(r"\s*#\s*$", "", line)
            stripped = line.strip()
        candidate = stripped.lstrip("#").strip() if stripped.startswith("#") else stripped
        if _is_noise_line(candidate, _rules=rules):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _score_content(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    char_len = len(content)
    line_count = len(lines)

    meaningful_lines: list[str] = []
    for ln in lines:
        if re.match(r"^\s*```", ln):
            continue
        meaningful_lines.append(ln)
    unique_source = meaningful_lines or lines
    unique_ratio = (len(set(unique_source)) / len(unique_source)) if unique_source else 0.0
    rules = load_noise_rules()
    noise_hits = sum(1 for ln in lines if _is_noise_line(ln, _rules=rules))
    noise_ratio = (noise_hits / line_count) if line_count else 0.0
    short_source = meaningful_lines or lines
    short_hits = sum(1 for ln in short_source if len(ln) <= 12)
    short_ratio = (short_hits / len(short_source)) if short_source else 0.0

    is_markdown_like = (
        "```" in content
        or bool(re.search(r"^\s*#{1,6}\s+\S", content, flags=re.M))
        or bool(re.search(r"^\s*[-*]\s+\S", content, flags=re.M))
    )
    paragraph_count = content.count("\n\n")
    code_fence_count = content.count("```")
    heading_count = len(re.findall(r"^\s*#{1,6}\s+\S", content, flags=re.M))
    bullet_count = len(re.findall(r"^\s*[-*]\s+\S", content, flags=re.M))
    structure_bonus = 0.0
    if is_markdown_like:
        structure_bonus += 6.0 if code_fence_count >= 2 else (3.0 if code_fence_count else 0.0)
        structure_bonus += min(6.0, float(paragraph_count))
        structure_bonus += min(4.0, float(line_count) / 8.0 * 4.0) if line_count else 0.0
        structure_bonus += min(2.0, float(heading_count))
        structure_bonus += min(2.0, float(bullet_count) / 3.0 * 2.0) if bullet_count else 0.0

    length_score = min(60.0, (char_len / 2000.0) * 60.0)
    unique_score = min(20.0, unique_ratio * 20.0)
    noise_penalty = min(50.0, noise_ratio * 100.0 * 0.7)

    short_line_penalty = 0.0
    if line_count >= 40 and short_ratio >= 0.6:
        short_line_penalty = min(30.0, (short_ratio - 0.6) * 100.0)

    score = max(
        0.0,
        min(100.0, length_score + unique_score - noise_penalty - short_line_penalty + structure_bonus),
    )
    return {
        "quality_score": int(round(score)),
        "char_len": char_len,
        "line_count": line_count,
        "unique_line_ratio": round(unique_ratio, 3),
        "noise_line_ratio": round(noise_ratio, 3),
    }


def _meta_content(soup: BeautifulSoup, attrs: dict[str, str]) -> str:
    tag = soup.find("meta", attrs=attrs)
    if not tag:
        return ""
    return (tag.get("content") or "").strip()


def _extract_title_and_description(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html or "", "lxml")
    title = (
        _meta_content(soup, {"property": "og:title"})
        or _meta_content(soup, {"name": "twitter:title"})
        or (soup.find("title").get_text(strip=True) if soup.find("title") else "")
    )
    description = (
        _meta_content(soup, {"property": "og:description"})
        or _meta_content(soup, {"name": "twitter:description"})
        or _meta_content(soup, {"name": "description"})
    )
    return title.strip(), description.strip()


def _trafilatura_extract(
    html: str,
    *,
    url: str | None,
    output_format: str,
    favor_precision: bool = False,
    favor_recall: bool = False,
    fast: bool = False,
    include_links: bool = False,
) -> str | None:
    try:
        from trafilatura import extract
    except Exception as e:
        logger.warning("trafilatura not available: %s", e)
        return None

    try:
        max_tree_size_raw = os.getenv("TRAFILATURA_MAX_TREE_SIZE", "").strip()
        max_tree_size = int(max_tree_size_raw) if max_tree_size_raw else None
    except Exception:
        max_tree_size = None

    try:
        return extract(
            html,
            url=url,
            output_format=output_format,
            include_comments=False,
            include_tables=True,
            include_images=False,
            include_links=include_links,
            deduplicate=True,
            favor_precision=favor_precision,
            favor_recall=favor_recall,
            fast=fast,
            max_tree_size=max_tree_size,
        )
    except Exception as e:
        logger.debug("trafilatura.extract failed: %s", e)
        return None


def _trafilatura_baseline(html: str) -> str | None:
    try:
        from trafilatura import baseline
    except Exception:
        return None
    try:
        _postbody, text, _len_text = baseline(html)
        return text
    except Exception:
        return None


def _extract_csdn_html_pruned(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    title = ""
    title_tag = soup.select_one("h1.title-article") or soup.select_one("h1")
    if title_tag:
        title = title_tag.get_text(strip=True)

    main = soup.select_one("#content_views") or soup.select_one("article")
    if not main:
        return None

    for selector in (
        "script",
        "style",
        "header",
        "footer",
        "nav",
        "aside",
        ".hide-article-box",
        ".recommend-box",
        ".tool-box",
        ".blog-tags-box",
        ".article-info-box",
        ".operating",
        ".csdn-toolbar",
        "#passportbox",
        "#toolBarBox",
    ):
        for node in main.select(selector):
            try:
                node.decompose()
            except Exception:
                pass

    title_html = f"<h1>{html_lib.escape(title)}</h1>" if title else ""
    return f"<html><body>{title_html}{str(main)}</body></html>"


def _extract_github_html_pruned(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    title, description = _extract_title_and_description(html)

    readme = (
        soup.select_one("#readme article.markdown-body")
        or soup.select_one("#readme .markdown-body")
        or soup.select_one("article.markdown-body")
    )
    if not readme:
        return None

    for selector in (
        "svg",
        "button",
        "summary",
        "details",
        "clipboard-copy",
        "a.anchor",
        "a.anchorjs-link",
        ".octicon",
    ):
        for node in readme.select(selector):
            try:
                node.decompose()
            except Exception:
                pass

    title_html = f"<h1>{html_lib.escape(title)}</h1>" if title else ""
    desc_html = f"<p>{html_lib.escape(description)}</p>" if description else ""
    return f"<html><body>{title_html}{desc_html}{str(readme)}</body></html>"


def _extract_discourse_html_pruned(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    root = soup.select_one("#main-outlet") or soup.select_one("main") or soup.body
    if not root:
        return None

    articles = root.select("article[data-post-id]") or root.select("article.topic-post")

    cooked_blocks: list[str] = []
    if articles:
        for article in articles:
            cooked = article.select_one(".cooked")
            if not cooked:
                continue
            for selector in ("svg", "button", ".post-menu-area", ".topic-map", ".names"):
                for node in cooked.select(selector):
                    try:
                        node.decompose()
                    except Exception:
                        pass
            cooked_blocks.append(str(cooked))
    else:
        for cooked in root.select(".cooked"):
            if not cooked:
                continue
            for selector in ("svg", "button", ".post-menu-area", ".topic-map", ".names"):
                for node in cooked.select(selector):
                    try:
                        node.decompose()
                    except Exception:
                        pass
            cooked_blocks.append(str(cooked))

    if not cooked_blocks:
        return None

    title, _ = _extract_title_and_description(html)
    title_html = f"<h1>{html_lib.escape(title)}</h1>" if title else ""
    body_html = "".join(cooked_blocks)
    return f"<html><body>{title_html}{body_html}</body></html>"


def _extract_bangumi_html_pruned(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    title, description = _extract_title_and_description(html)

    col_a = soup.select_one("#columnA")
    col_b = soup.select_one("#columnB")
    if not col_a and not col_b:
        return None

    parts: list[str] = []
    if title:
        parts.append(f"<h1>{html_lib.escape(title)}</h1>")
    if description:
        parts.append(f"<p>{html_lib.escape(description)}</p>")
    if col_a:
        parts.append(str(col_a))
    if col_b:
        parts.append(str(col_b))

    combined = "".join(parts)
    return f"<html><body>{combined}</body></html>"


def _extract_steamcommunity_html_pruned(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    title, description = _extract_title_and_description(html)

    main = soup.select_one("#responsive_page_template_content") or soup.select_one(".responsive_page_template_content")
    if not main:
        return None

    for selector in (
        "#global_header",
        "#global_actions",
        "#footer",
        ".responsive_page_menu_ctn",
        ".responsive_header",
        ".responsive_page_menu",
        ".responsive_local_menu",
        ".pulldown",
    ):
        for node in main.select(selector):
            try:
                node.decompose()
            except Exception:
                pass

    title_html = f"<h1>{html_lib.escape(title)}</h1>" if title else ""
    desc_html = f"<p>{html_lib.escape(description)}</p>" if description else ""
    return f"<html><body>{title_html}{desc_html}{str(main)}</body></html>"


def _extract_discourse_text_pruned(html: str, *, url: str) -> str | None:
    del url
    title, _ = _extract_title_and_description(html)
    title = (title or "").strip()
    if not title:
        return None

    topic_title = re.split(r"\s+-\s+", title, maxsplit=1)[0].strip()
    if not topic_title or len(topic_title) < 4:
        return None

    raw_text = html_to_text(html)
    if not raw_text:
        return None
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if not lines:
        return None

    start_idx = 0
    for i, ln in enumerate(lines):
        if ln == topic_title:
            start_idx = i
            break
    else:
        for i, ln in enumerate(lines):
            if topic_title in ln and len(ln) <= len(topic_title) + 12:
                start_idx = i
                break

    end_idx = len(lines)
    for marker in ("相关话题", "话题列表", "Related topics", "Topic list"):
        for i in range(start_idx + 1, len(lines)):
            if lines[i] == marker or marker in lines[i]:
                end_idx = min(end_idx, i)
                break

    sliced = lines[start_idx:end_idx]

    cleaned_lines: list[str] = []
    for ln in sliced:
        if not ln:
            continue
        if re.fullmatch(r"\d{1,2}", ln):
            continue
        if re.fullmatch(r"\d+\s*/\s*\d+", ln):
            continue
        cleaned_lines.append(ln)

    kept = "\n".join(cleaned_lines).strip()
    kept = _clean_extracted_text(kept)
    return kept or None


def _build_degraded_markdown(html: str) -> str | None:
    title, description = _extract_title_and_description(html)
    if not title and not description:
        return None
    parts = []
    if title:
        parts.append(f"# {title}")
    if description:
        parts.append(description)
    return "\n\n".join(parts).strip()


def _build_degraded_text(html: str) -> str | None:
    title, description = _extract_title_and_description(html)
    if not title and not description:
        return None
    parts = []
    if title:
        parts.append(title)
    if description:
        parts.append(description)
    return "\n\n".join(parts).strip()


def _extractor_bonus(extractor: str, *, tuning: dict[str, Any]) -> int:
    if extractor.startswith("adapter:"):
        return int(tuning["bonus_adapter"])
    if extractor.startswith("trafilatura:precision"):
        return int(tuning["bonus_precision"])
    if extractor.startswith("trafilatura:recall"):
        return int(tuning["bonus_recall"])
    if extractor.startswith("trafilatura:fast"):
        return int(tuning["bonus_fast"])
    if extractor.startswith("trafilatura:baseline"):
        return int(tuning["bonus_baseline"])
    return 0


def _rank_key(item: dict[str, Any], *, tuning: dict[str, Any]) -> tuple[int, int, int]:
    q = int(item.get("quality_score", 0) or 0)
    bonus = _extractor_bonus(item.get("extractor", "") or "", tuning=tuning)
    char_len = int(item.get("char_len", 0) or 0)
    return (q + bonus, q, char_len)


def _is_high_quality_candidate(item: dict[str, Any], *, min_chars: int, min_quality: int) -> bool:
    return int(item.get("char_len", 0) or 0) >= min_chars and int(item.get("quality_score", 0) or 0) >= min_quality


def _extract_best_content(html: str, *, url: str, output_format: str) -> dict[str, Any]:
    cfg = get_config()
    host = get_hostname(url)
    strategy = (cfg.extraction.strategy or "quality").lower()

    min_chars = cfg.extraction.markdown_min_chars if output_format == "markdown" else cfg.extraction.text_min_chars

    candidates: list[dict[str, Any]] = []
    seen_cleaned: set[str] = set()

    tuning = _INTERNAL_TUNING.get(strategy, _INTERNAL_TUNING["quality"])
    quality_first = strategy == "quality"
    speed_first = strategy == "speed"

    def _clean_for_mode(text: str) -> str:
        return _clean_extracted_markdown(text) if output_format == "markdown" else _clean_extracted_text(text)

    def _add_candidate(content: str | None, extractor: str) -> dict[str, Any] | None:
        if not content:
            return None
        cleaned = _clean_for_mode(content)
        if not cleaned:
            return None
        if cleaned in seen_cleaned:
            return None
        seen_cleaned.add(cleaned)

        metrics = _score_content(cleaned)
        item = {
            "content": cleaned,
            "extractor": extractor,
            **metrics,
            "degraded": False,
        }
        candidates.append(item)
        return item

    def _should_early_stop(item: dict[str, Any] | None) -> bool:
        if not item or not bool(tuning["early_stop_enabled"]):
            return False
        if not quality_first:
            return False
        early_chars = max(min_chars, int(tuning["early_stop_chars"]))
        return _is_high_quality_candidate(
            item,
            min_chars=early_chars,
            min_quality=int(tuning["early_stop_quality"]),
        )

    def _run_adapter_candidates() -> dict[str, Any] | None:
        if host.endswith("csdn.net"):
            pruned = _extract_csdn_html_pruned(html)
            if pruned:
                item = _add_candidate(
                    _trafilatura_extract(pruned, url=url, output_format=output_format, favor_precision=True),
                    "adapter:csdn+trafilatura",
                )
                if _should_early_stop(item):
                    return item

        if host.endswith("github.com"):
            pruned = _extract_github_html_pruned(html)
            if pruned:
                item = _add_candidate(
                    _trafilatura_extract(
                        pruned,
                        url=url,
                        output_format=output_format,
                        favor_precision=True,
                        include_links=True,
                    ),
                    "adapter:github+trafilatura",
                )
                if _should_early_stop(item):
                    return item

        if host.endswith(("bgm.tv", "bangumi.tv", "chii.in")):
            pruned = _extract_bangumi_html_pruned(html)
            if pruned:
                item = _add_candidate(_trafilatura_baseline(pruned), "adapter:bangumi+baseline")
                if _should_early_stop(item):
                    return item
                item = _add_candidate(html_to_text(pruned), "adapter:bangumi+bs4")
                if _should_early_stop(item):
                    return item

        if host.endswith("steamcommunity.com"):
            pruned = _extract_steamcommunity_html_pruned(html)
            if pruned:
                item = _add_candidate(_trafilatura_baseline(pruned), "adapter:steamcommunity+baseline")
                if _should_early_stop(item):
                    return item
                item = _add_candidate(html_to_text(pruned), "adapter:steamcommunity+bs4")
                if _should_early_stop(item):
                    return item

        if "/t/" in (url or ""):
            pruned = _extract_discourse_html_pruned(html)
            if pruned:
                item = _add_candidate(
                    _trafilatura_extract(
                        pruned,
                        url=url,
                        output_format=output_format,
                        favor_precision=True,
                        include_links=True,
                    ),
                    "adapter:discourse+trafilatura",
                )
                if _should_early_stop(item):
                    return item
            item = _add_candidate(_extract_discourse_text_pruned(html, url=url), "adapter:discourse:text_pruned")
            if _should_early_stop(item):
                return item

        return None

    def _run_core_candidates() -> dict[str, Any] | None:
        # Keep extractor order strategy-dependent to reduce unnecessary expensive paths.
        plans: list[tuple[str, Any]] = []
        plans.append(
            (
                "trafilatura:precision",
                lambda: _trafilatura_extract(html, url=url, output_format=output_format, favor_precision=True),
            )
        )

        if not speed_first:
            plans.append(
                (
                    "trafilatura:recall",
                    lambda: _trafilatura_extract(html, url=url, output_format=output_format, favor_recall=True),
                )
            )

        plans.append(
            (
                "trafilatura:fast",
                lambda: _trafilatura_extract(
                    html,
                    url=url,
                    output_format=output_format,
                    favor_precision=True,
                    fast=True,
                ),
            )
        )

        if not speed_first:
            plans.append(("trafilatura:baseline", lambda: _trafilatura_baseline(html)))

        plans.append(("bs4:text", lambda: html_to_text(html)))

        for extractor, fn in plans:
            item = _add_candidate(fn(), extractor)
            if _should_early_stop(item):
                return item

        return None

    early = _run_adapter_candidates()
    if early:
        return early

    early = _run_core_candidates()
    if early:
        return early

    ranked = sorted(candidates, key=lambda x: _rank_key(x, tuning=tuning), reverse=True)

    adapter_candidates = [c for c in ranked if (c.get("extractor", "") or "").startswith("adapter:")]
    for candidate in adapter_candidates:
        if _is_high_quality_candidate(
            candidate,
            min_chars=min_chars,
            min_quality=int(tuning["adapter_min_quality"]),
        ):
            return candidate

    for candidate in ranked:
        if _is_high_quality_candidate(
            candidate,
            min_chars=min_chars,
            min_quality=int(tuning["general_min_quality"]),
        ):
            return candidate

    # In speed mode, keep best non-empty candidate if present.
    if speed_first and ranked:
        return ranked[0]

    degraded = _build_degraded_markdown(html) if output_format == "markdown" else _build_degraded_text(html)
    if degraded:
        cleaned = _clean_for_mode(degraded)
        metrics = _score_content(cleaned)
        return {
            "content": cleaned,
            "extractor": "meta:degraded",
            **metrics,
            "degraded": True,
        }

    return ranked[0] if ranked else {
        "content": "",
        "extractor": "none",
        "quality_score": 0,
        "char_len": 0,
        "line_count": 0,
        "unique_line_ratio": 0.0,
        "noise_line_ratio": 0.0,
        "degraded": True,
    }


def _extract_metadata(html: str) -> dict[str, Any]:
    cfg = get_config()
    soup = BeautifulSoup(html or "", "lxml")

    title = (
        _meta_content(soup, {"property": "og:title"})
        or _meta_content(soup, {"name": "twitter:title"})
        or (soup.find("title").get_text(strip=True) if soup.find("title") else "")
    )

    description = (
        _meta_content(soup, {"property": "og:description"})
        or _meta_content(soup, {"name": "twitter:description"})
        or _meta_content(soup, {"name": "description"})
    )

    canonical_url = ""
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical:
        canonical_url = (canonical.get("href") or "").strip()

    links = []
    for a in soup.find_all("a", href=True, limit=50):
        links.append({"text": a.get_text(strip=True), "href": a["href"]})

    links_str = str(links)
    _, was_truncated = limit_content_length(links_str)
    if was_truncated:
        avg_length = len(links_str) / len(links) if links else 0
        keep_count = max(1, int(cfg.max_token_limit * 4 / avg_length) if avg_length > 0 else 0)
        links = links[:keep_count]

    return {
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "links": links,
        "truncated": was_truncated,
    }
