"""Local smoke checks for code wiring and config values.

Usage:
  python -m websearch.test.smoke_check
  python -m websearch.test.smoke_check --require-llm
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List

from websearch.tools.search import fetch, mcp, web_search
from websearch.utils.config import get_config, init_runtime
from websearch.utils.content_parse import (
    extract_browse_page_links,
    parse_markdown_links,
    strip_urls,
)
from websearch.utils.url_helpers import normalize_url_for_dedup, unwrap_redirect_url


def _is_placeholder_api_key(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return True
    placeholder_patterns = (
        r"^sk-xxx+$",
        r"your.*key",
        r"example",
        r"test",
        r"dummy",
        r"placeholder",
    )
    return any(re.search(pattern, text) for pattern in placeholder_patterns)


def _run_core_checks() -> List[str]:
    failures: List[str] = []

    if unwrap_redirect_url(
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3Dc"
    ) != "https://example.com/a?b=c":
        failures.append("DDG redirect unwrap failed")

    links, summary = parse_markdown_links(
        "[Example](https://example.com/a) bare https://example.com/b"
    )
    urls = {x.get("url") for x in links}
    if "https://example.com/a" not in urls or "https://example.com/b" not in urls:
        failures.append("Markdown link parser failed")

    cleaned = strip_urls(summary)
    if "http://" in cleaned or "https://" in cleaned:
        failures.append("URL stripping failed")

    browse_links = extract_browse_page_links(
        'browse_page {"url":"https://openai.com/","instructions":"check"}'
    )
    if not browse_links or browse_links[0].get("url") != "https://openai.com/":
        failures.append("browse_page link extraction failed")

    normalized = normalize_url_for_dedup("https://example.com/path/?utm_source=x")
    if normalized != "https://example.com/path":
        failures.append("URL normalization failed")

    return failures


def _run_config_checks(require_llm: bool) -> Dict[str, object]:
    cfg = get_config()
    warnings: List[str] = []
    failures: List[str] = []

    key_is_placeholder = _is_placeholder_api_key(cfg.openai_api_key or "")
    llm_ready = cfg.llm_configured and not key_is_placeholder

    if not cfg.openai_base_url:
        warnings.append("OPENAI_BASE_URL is empty; AI summary will be disabled")
    if not cfg.openai_api_key:
        warnings.append("OPENAI_API_KEY is empty; AI summary will be disabled")
    elif key_is_placeholder:
        warnings.append("OPENAI_API_KEY looks like a placeholder value")

    if require_llm and not llm_ready:
        failures.append("LLM strict check failed: provide real OPENAI_API_KEY + OPENAI_BASE_URL")

    if cfg.playwright_timeout_ms <= 0:
        failures.append("PLAYWRIGHT_TIMEOUT_MS must be > 0")
    if cfg.playwright_challenge_wait <= 0:
        failures.append("PLAYWRIGHT_CHALLENGE_WAIT must be > 0")

    return {
        "warnings": warnings,
        "failures": failures,
        "snapshot": {
            "OPENAI_MODEL": cfg.openai_model,
            "OPENAI_BASE_URL_set": bool(cfg.openai_base_url),
            "OPENAI_API_KEY_set": bool(cfg.openai_api_key),
            "OPENAI_API_KEY_placeholder": key_is_placeholder,
            "LLM_effectively_ready": llm_ready,
            "PROXY_CONFIG": cfg.proxy,
            "CF_WORKER_URL": cfg.cf_worker_url,
            "PLAYWRIGHT_FALLBACK": cfg.playwright_fallback,
            "PLAYWRIGHT_TIMEOUT_MS": cfg.playwright_timeout_ms,
            "PLAYWRIGHT_CHALLENGE_WAIT": cfg.playwright_challenge_wait,
            "imports_ok": {
                "fetch": callable(fetch),
                "web_search": callable(web_search),
                "mcp_obj": mcp is not None,
            },
        },
    }


def main() -> None:
    init_runtime()
    parser = argparse.ArgumentParser(description="WebSearch local smoke check")
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="Fail if OPENAI config is missing or looks like placeholder",
    )
    args = parser.parse_args()

    core_failures = _run_core_checks()
    config_result = _run_config_checks(require_llm=args.require_llm)
    config_failures = list(config_result["failures"])
    failures = core_failures + config_failures

    result = {
        "success": len(failures) == 0,
        "checks": {
            "core_failures": core_failures,
            "config_failures": config_failures,
            "warnings": config_result["warnings"],
        },
        "config_snapshot": config_result["snapshot"],
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["success"] else 2)


if __name__ == "__main__":
    main()
