"""Robust .env parser utilities."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _find_unescaped_quote(text: str, quote: str) -> int:
    escaped = False
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == quote:
            return idx
    return -1


def _unescape_quoted_value(value: str, quote: str) -> str:
    if quote not in ("'", '"'):
        return value
    result: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n:
            nxt = value[i + 1]
            if quote == '"':
                mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
                if nxt in mapping:
                    result.append(mapping[nxt])
                    i += 2
                    continue
            elif nxt in {"\\", "'"}:
                result.append(nxt)
                i += 2
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def _strip_inline_comment_unquoted(value: str) -> str:
    escaped = False
    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "#" and (idx == 0 or value[idx - 1].isspace()):
            return value[:idx].rstrip()
    return value.rstrip()


def _parse_env_value(value_part: str, lines: list[str], start_idx: int) -> tuple[str, int]:
    value_part = value_part.lstrip()
    if not value_part:
        return "", start_idx

    quote = value_part[0]
    if quote not in ("'", '"'):
        return _strip_inline_comment_unquoted(value_part).strip(), start_idx

    idx = start_idx
    buffer = value_part[1:]
    while True:
        end = _find_unescaped_quote(buffer, quote)
        if end >= 0:
            inner = buffer[:end]
            return _unescape_quoted_value(inner, quote), idx
        idx += 1
        if idx >= len(lines):
            return _unescape_quoted_value(buffer, quote), idx - 1
        buffer += "\n" + lines[idx]


def load_env_file(path: Path) -> None:
    """Parse .env-like file and set missing values into os.environ."""

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return
    except OSError as e:
        print(f"[config] failed to read env file '{path}': {e}", file=sys.stderr)
        return

    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        raw = line.strip()
        if not raw or raw.startswith("#"):
            idx += 1
            continue
        if raw.startswith("export "):
            raw = raw[len("export ") :].strip()
        if "=" not in raw:
            idx += 1
            continue
        key, value_part = raw.split("=", 1)
        key = key.strip()
        if not key:
            idx += 1
            continue
        value, idx = _parse_env_value(value_part, lines, idx)
        os.environ.setdefault(key, value)
        idx += 1

