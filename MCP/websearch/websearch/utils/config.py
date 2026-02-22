"""Runtime configuration and logging bootstrap for WebSearch."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .env_parser import load_env_file

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

_RUNTIME_CONFIG: Optional["AppConfig"] = None
_LOGGING_READY = False


def _parse_viewport(raw: str | None) -> dict[str, int] | None:
    if not raw:
        return None
    text = raw.lower().replace(" ", "")
    if "x" in text:
        w, h = text.split("x", 1)
    elif "," in text:
        w, h = text.split(",", 1)
    else:
        return None
    try:
        return {"width": int(w), "height": int(h)}
    except ValueError:
        return None


@dataclass(frozen=True)
class PlaywrightConfig:
    headless: bool
    user_agent: str
    accept_language: str
    locale: str
    timezone_id: str
    viewport: dict[str, int] | None
    device_scale_factor: float
    executable_path: str | None


@dataclass(frozen=True)
class ExtractionConfig:
    strategy: str
    markdown_min_chars: int
    text_min_chars: int


@dataclass(frozen=True)
class AppConfig:
    proxy: Optional[str]
    cf_worker_url: Optional[str]
    openai_api_key: Optional[str]
    openai_base_url: Optional[str]
    openai_model: str
    user_agent: str
    max_token_limit: int
    curl_impersonate: str
    http_version: str
    fetch_timeout_s: int
    search_timeout_s: int
    search_result_limit: int
    playwright_fallback: bool
    playwright_timeout_ms: int
    playwright_challenge_wait: int
    search_max_per_domain: int
    playwright: PlaywrightConfig
    extraction: ExtractionConfig
    log_level: str

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_base_url)

    @property
    def proxies(self) -> Optional[dict[str, str]]:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}


def _normalize_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text if text else None


def _normalize_log_level(value: Optional[str]) -> str:
    text = (value or "INFO").strip().upper()
    level = getattr(logging, text, None)
    if isinstance(level, int):
        return text
    print(f"[config] invalid LOG_LEVEL '{value}', fallback to INFO", file=sys.stderr)
    return "INFO"


def _parse_bool(
    value: Optional[str],
    *,
    default: bool,
    field_name: str,
) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    print(f"[config] invalid boolean for {field_name}: '{value}', fallback to {default}", file=sys.stderr)
    return default


def _parse_int(
    value: Optional[str],
    *,
    default: int,
    minimum: Optional[int] = None,
    field_name: str,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except Exception:
        print(f"[config] invalid integer for {field_name}: '{value}', fallback to {default}", file=sys.stderr)
        return default
    if minimum is not None and parsed < minimum:
        print(
            f"[config] {field_name}={parsed} is below minimum {minimum}, fallback to {default}",
            file=sys.stderr,
        )
        return default
    return parsed


def _parse_choice(
    value: Optional[str],
    *,
    default: str,
    choices: set[str],
    field_name: str,
) -> str:
    if value is None:
        return default
    text = value.strip().lower()
    if text in choices:
        return text
    ordered = "/".join(sorted(choices))
    print(
        f"[config] invalid value for {field_name}: '{value}', expected {ordered}, fallback to {default}",
        file=sys.stderr,
    )
    return default


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WebSearch MCP Server")
    parser.add_argument("--proxy", type=str, default=None, help="Local proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--cf-worker", type=str, default=None, help="Cloudflare Worker URL")
    parser.add_argument("--openai-api-key", type=str, default=None, help="OpenAI API key")
    parser.add_argument("--openai-base-url", type=str, default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--openai-model", type=str, default=None, help="OpenAI model name")
    parser.add_argument("--log-level", type=str, default=None, help="DEBUG/INFO/WARNING/ERROR/CRITICAL")
    return parser


def _pick(
    cli_value: Optional[str],
    env: Mapping[str, str],
    env_key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    if cli_value is not None:
        return cli_value
    return env.get(env_key, default)


def build_config(
    argv: Optional[Sequence[str]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> AppConfig:
    """Build AppConfig from argv and environment variables."""

    env_map: Mapping[str, str] = env if env is not None else os.environ
    parser = _build_parser()
    args, _ = parser.parse_known_args(list(argv) if argv is not None else None)

    proxy = _normalize_optional(_pick(args.proxy, env_map, "PROXY"))
    cf_worker_url = _normalize_optional(_pick(args.cf_worker, env_map, "CF_WORKER"))
    openai_api_key = _normalize_optional(_pick(args.openai_api_key, env_map, "OPENAI_API_KEY"))
    openai_base_url = _normalize_optional(_pick(args.openai_base_url, env_map, "OPENAI_BASE_URL"))
    openai_model = _normalize_optional(_pick(args.openai_model, env_map, "OPENAI_MODEL", "gpt-4o")) or "gpt-4o"
    log_level = _normalize_log_level(_pick(args.log_level, env_map, "LOG_LEVEL", "INFO"))

    user_agent = _normalize_optional(env_map.get("USER_AGENT")) or _DEFAULT_USER_AGENT
    curl_impersonate = _normalize_optional(env_map.get("CURL_IMPERSONATE")) or "chrome110"
    http_version = _normalize_optional(env_map.get("HTTP_VERSION")) or "v1"
    max_token_limit = _parse_int(
        env_map.get("MAX_TOKEN_LIMIT"),
        default=10000,
        minimum=1,
        field_name="MAX_TOKEN_LIMIT",
    )
    fetch_timeout_s = _parse_int(
        env_map.get("FETCH_TIMEOUT_S"),
        default=15,
        minimum=1,
        field_name="FETCH_TIMEOUT_S",
    )
    search_timeout_s = _parse_int(
        env_map.get("SEARCH_TIMEOUT_S"),
        default=60,
        minimum=1,
        field_name="SEARCH_TIMEOUT_S",
    )
    search_result_limit = _parse_int(
        env_map.get("SEARCH_RESULT_LIMIT"),
        default=25,
        minimum=1,
        field_name="SEARCH_RESULT_LIMIT",
    )
    playwright_fallback = _parse_bool(
        env_map.get("PLAYWRIGHT_FALLBACK"),
        default=True,
        field_name="PLAYWRIGHT_FALLBACK",
    )
    playwright_timeout_ms = _parse_int(
        env_map.get("PLAYWRIGHT_TIMEOUT_MS"),
        default=60000,
        minimum=1,
        field_name="PLAYWRIGHT_TIMEOUT_MS",
    )
    playwright_challenge_wait = _parse_int(
        env_map.get("PLAYWRIGHT_CHALLENGE_WAIT"),
        default=20,
        minimum=1,
        field_name="PLAYWRIGHT_CHALLENGE_WAIT",
    )
    extraction_strategy = _parse_choice(
        env_map.get("EXTRACTION_STRATEGY"),
        default="quality",
        choices={"quality", "balanced", "speed"},
        field_name="EXTRACTION_STRATEGY",
    )
    extraction_markdown_min_chars = _parse_int(
        env_map.get("EXTRACTION_MARKDOWN_MIN_CHARS"),
        default=120,
        minimum=1,
        field_name="EXTRACTION_MARKDOWN_MIN_CHARS",
    )
    extraction_text_min_chars = _parse_int(
        env_map.get("EXTRACTION_TEXT_MIN_CHARS"),
        default=200,
        minimum=1,
        field_name="EXTRACTION_TEXT_MIN_CHARS",
    )
    extraction = ExtractionConfig(
        strategy=extraction_strategy,
        markdown_min_chars=extraction_markdown_min_chars,
        text_min_chars=extraction_text_min_chars,
    )

    search_max_per_domain = _parse_int(
        env_map.get("SEARCH_MAX_PER_DOMAIN"),
        default=2,
        minimum=0,
        field_name="SEARCH_MAX_PER_DOMAIN",
    )

    pw_user_agent = _normalize_optional(env_map.get("PW_USER_AGENT")) or user_agent
    pw_executable = (
        _normalize_optional(env_map.get("PW_CHROMIUM_EXECUTABLE_PATH"))
        or _normalize_optional(env_map.get("PW_EXECUTABLE_PATH"))
        or _normalize_optional(env_map.get("PLAYWRIGHT_EXECUTABLE_PATH"))
    )
    try:
        pw_device_scale = float(env_map.get("PW_DEVICE_SCALE", "2"))
    except (ValueError, TypeError):
        pw_device_scale = 2.0

    playwright_cfg = PlaywrightConfig(
        headless=_parse_bool(env_map.get("PW_HEADLESS"), default=True, field_name="PW_HEADLESS"),
        user_agent=pw_user_agent,
        accept_language=_normalize_optional(env_map.get("PW_ACCEPT_LANGUAGE")) or _DEFAULT_ACCEPT_LANGUAGE,
        locale=_normalize_optional(env_map.get("PW_LOCALE")) or "zh-CN",
        timezone_id=_normalize_optional(env_map.get("PW_TIMEZONE")) or "Asia/Shanghai",
        viewport=_parse_viewport(env_map.get("PW_VIEWPORT", "1366x768")),
        device_scale_factor=pw_device_scale,
        executable_path=pw_executable,
    )

    return AppConfig(
        proxy=proxy,
        cf_worker_url=cf_worker_url,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_model=openai_model,
        user_agent=user_agent,
        max_token_limit=max_token_limit,
        curl_impersonate=curl_impersonate,
        http_version=http_version,
        fetch_timeout_s=fetch_timeout_s,
        search_timeout_s=search_timeout_s,
        search_result_limit=search_result_limit,
        playwright_fallback=playwright_fallback,
        playwright_timeout_ms=playwright_timeout_ms,
        playwright_challenge_wait=playwright_challenge_wait,
        search_max_per_domain=search_max_per_domain,
        playwright=playwright_cfg,
        extraction=extraction,
        log_level=log_level,
    )


def setup_logging(level_name: str, stream: Optional[object] = None) -> None:
    """Initialize root logging once and keep it idempotent."""

    global _LOGGING_READY

    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()

    if stream is None:
        try:
            stream = open(
                sys.stderr.fileno(),
                mode="w",
                encoding="utf-8",
                errors="replace",
                closefd=False,
            )
        except Exception:
            stream = sys.stderr

    if not _LOGGING_READY:
        root.handlers = []
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handler._websearch_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    else:
        existing = [h for h in root.handlers if getattr(h, "_websearch_handler", False)]
        if not existing:
            handler = logging.StreamHandler(stream)
            handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
            handler._websearch_handler = True  # type: ignore[attr-defined]
            root.addHandler(handler)

    root.setLevel(level)
    for handler in root.handlers:
        if getattr(handler, "_websearch_handler", False):
            handler.setLevel(level)

    _LOGGING_READY = True


def init_runtime(argv: Optional[Sequence[str]] = None) -> AppConfig:
    """Load .env, build runtime config, and setup logging."""

    global _RUNTIME_CONFIG

    load_env_file(_ENV_PATH)
    cfg = build_config(argv=argv, env=os.environ)
    setup_logging(cfg.log_level)
    _RUNTIME_CONFIG = cfg

    logger = logging.getLogger(__name__)
    logger.debug("[DEBUG] argv=%s", list(argv) if argv is not None else sys.argv)
    logger.debug("[DEBUG] proxy=%s cf_worker=%s", cfg.proxy, cfg.cf_worker_url)
    logger.debug(
        "[DEBUG] openai: key=%s base=%s model=%s",
        "***" if cfg.openai_api_key else None,
        cfg.openai_base_url,
        cfg.openai_model,
    )
    return cfg


def get_config() -> AppConfig:
    """Return runtime config; requires init_runtime() first."""

    if _RUNTIME_CONFIG is None:
        raise RuntimeError("Runtime config is not initialized. Call init_runtime() before using WebSearch modules.")
    return _RUNTIME_CONFIG


def _reset_runtime_for_tests() -> None:
    """Reset runtime globals for isolated tests."""

    global _RUNTIME_CONFIG, _LOGGING_READY
    _RUNTIME_CONFIG = None
    _LOGGING_READY = False
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, "_websearch_handler", False)]


__all__ = [
    "AppConfig",
    "ExtractionConfig",
    "PlaywrightConfig",
    "build_config",
    "get_config",
    "init_runtime",
    "setup_logging",
]
