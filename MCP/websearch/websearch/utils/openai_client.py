"""OpenAI-compatible chat completions client."""

from __future__ import annotations

import json
import logging
from typing import Any

from curl_cffi import requests as curl_requests

from .config import get_config
from .proxy import get_proxies

logger = logging.getLogger(__name__)


def call_openai_chat_completions(prompt: str) -> tuple[str, str]:
    cfg = get_config()
    if not cfg.openai_api_key or not cfg.openai_base_url:
        raise RuntimeError("OpenAI client not configured")

    url = f"{cfg.openai_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": cfg.openai_model,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {cfg.openai_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    response = curl_requests.post(
        url,
        json=payload,
        headers=headers,
        proxies=get_proxies(),
        allow_redirects=True,
        impersonate=cfg.curl_impersonate,
        http_version=cfg.http_version,
        stream=True,
    )
    response.raise_for_status()

    content_type = (response.headers.get("content-type") or "").lower()
    is_sse = "text/event-stream" in content_type

    if is_sse:
        return _consume_sse_stream(response)

    return _consume_json_response(response)


def _consume_sse_stream(response: curl_requests.Response) -> tuple[str, str]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    pending = ""
    done = False

    def _consume_line(raw_line: str) -> None:
        nonlocal done
        line = (raw_line or "").strip()
        if not line.startswith("data:"):
            return
        data = line[len("data:"):].strip()
        if not data:
            return
        if data == "[DONE]":
            done = True
            return
        try:
            obj = json.loads(data)
        except Exception:
            return
        for choice in obj.get("choices") or []:
            delta = (choice or {}).get("delta") or {}
            if not isinstance(delta, dict):
                continue
            piece = delta.get("content")
            if piece:
                content_parts.append(str(piece))
            reasoning_piece = (
                delta.get("reasoning_content")
                or delta.get("reasoning")
                or delta.get("analysis")
                or delta.get("thinking")
            )
            if reasoning_piece:
                reasoning_parts.append(str(reasoning_piece))

    try:
        for chunk in response.iter_content():
            if not chunk:
                continue
            pending += chunk.decode("utf-8", errors="ignore")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                _consume_line(line.rstrip("\r"))
                if done:
                    break
            if done:
                break
    except Exception as e:
        if not content_parts and not reasoning_parts:
            raise
        logger.warning("AI SSE stream interrupted, returning partial: %s", e)
    finally:
        try:
            response.close()
        except Exception:
            pass

    if pending and not done:
        _consume_line(pending.rstrip("\r"))
    return "".join(content_parts), "".join(reasoning_parts)


def _consume_json_response(response: curl_requests.Response) -> tuple[str, str]:
    raw_chunks: list[bytes] = []
    try:
        for chunk in response.iter_content():
            if chunk:
                raw_chunks.append(chunk)
    finally:
        try:
            response.close()
        except Exception:
            pass
    text = b"".join(raw_chunks).decode("utf-8", errors="replace")

    try:
        data = json.loads(text)
    except Exception:
        return text, ""

    content = ""
    reasoning = ""
    try:
        choice0 = (data.get("choices") or [{}])[0]
        message = choice0.get("message") or {}
        content_value = message.get("content") or ""
        reasoning_value = (
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("analysis")
            or ""
        )
        if isinstance(content_value, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content_value
            )
        else:
            content = str(content_value)
        if isinstance(reasoning_value, list):
            reasoning = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in reasoning_value
            )
        else:
            reasoning = str(reasoning_value)
    except Exception:
        pass

    return content, reasoning
