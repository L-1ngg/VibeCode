"""Proxy and CF Worker URL helpers."""

from __future__ import annotations

from urllib.parse import quote

from .config import get_config


def get_target_url(original_url: str) -> str:
    cfg = get_config()
    if cfg.cf_worker_url:
        worker_base = cfg.cf_worker_url.rstrip("/")
        encoded_target = quote(original_url)
        return f"{worker_base}?url={encoded_target}"
    return original_url


def get_proxies() -> dict[str, str] | None:
    return get_config().proxies
