"""Load noise filtering rules from UTF-8 text files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Pattern, Tuple

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).resolve().parent / "rules"
_RULE_FILES = ("noise_zh.txt", "noise_en.txt")

_FALLBACK_REGEX_RULES = (
    r"^\s*(skip to main content|back to top|reload|dismiss alert)\s*$",
    r"^\s*(repository files navigation|view all files)\s*$",
    r"^\s*(登录|注册|请先登录|立即登录)\s*$",
    r"^\s*(点赞|收藏|分享|评论|关注|举报)\s*$",
)
_FALLBACK_SUBSTRINGS = (
    "打开app",
    "下载app",
    "访问异常",
    "安全验证",
    "captcha",
    "robot check",
)

_CACHE: Tuple[Tuple[Pattern[str], ...], Tuple[str, ...]] | None = None


def _parse_rule_file(path: Path) -> tuple[list[str], list[str]]:
    regex_rules: list[str] = []
    substring_rules: list[str] = []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logger.debug("Noise rule file not found: %s", path)
        return regex_rules, substring_rules
    except OSError as e:
        logger.warning("Failed to read noise rule file %s: %s", path, e)
        return regex_rules, substring_rules

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            pattern = line[3:].strip()
            if pattern:
                regex_rules.append(pattern)
            continue
        if line.startswith("sub:"):
            needle = line[4:].strip().lower()
            if needle:
                substring_rules.append(needle)
            continue
        substring_rules.append(line.lower())

    return regex_rules, substring_rules


def load_noise_rules() -> tuple[tuple[Pattern[str], ...], tuple[str, ...]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    regex_sources: list[str] = []
    substring_sources: list[str] = []

    for filename in _RULE_FILES:
        regex_items, substring_items = _parse_rule_file(_RULES_DIR / filename)
        regex_sources.extend(regex_items)
        substring_sources.extend(substring_items)

    if not regex_sources:
        regex_sources.extend(_FALLBACK_REGEX_RULES)
    if not substring_sources:
        substring_sources.extend(_FALLBACK_SUBSTRINGS)

    compiled: list[Pattern[str]] = []
    for rule in regex_sources:
        try:
            compiled.append(re.compile(rule, re.IGNORECASE))
        except re.error as e:
            logger.warning("Invalid noise regex skipped: %s (%s)", rule, e)

    _CACHE = (tuple(compiled), tuple(substring_sources))
    return _CACHE


def reset_noise_rules_cache() -> None:
    global _CACHE
    _CACHE = None

