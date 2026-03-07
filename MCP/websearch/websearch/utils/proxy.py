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


def apply_worker_auth(headers: dict[str, str] | None, request_url: str) -> dict[str, str] | None:
    cfg = get_config()
    merged = dict(headers or {})
    worker_base = (cfg.cf_worker_url or "").rstrip("/")
    if not worker_base or not request_url.startswith(worker_base):
        return merged or None

    if cfg.cf_worker_token and "Authorization" not in merged and "x-api-key" not in merged:
        merged["Authorization"] = f"Bearer {cfg.cf_worker_token}"
    return merged or None


def get_proxies() -> dict[str, str] | None:
    return get_config().proxies
