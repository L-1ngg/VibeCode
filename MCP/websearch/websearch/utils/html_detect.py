"""HTML block/challenge detection and HTML-to-text conversion."""

from __future__ import annotations

from bs4 import BeautifulSoup


def looks_like_challenge_text(content: str) -> bool:
    lowered = (content or "").lower()
    return (
        "just a moment" in lowered
        or "checking your browser" in lowered
        or "attention required" in lowered
        or "cf-browser-verification" in lowered
        or ("cloudflare" in lowered and "ray id" in lowered)
    )


def looks_like_blocked_text(content: str) -> bool:
    """Detect generic blocks/login walls/captcha pages (best-effort)."""
    if not content:
        return False
    if looks_like_challenge_text(content):
        return True

    visible_text = content
    if "<" in content and ">" in content:
        try:
            visible_text = html_to_text(content)
        except Exception:
            visible_text = content

    lowered = (visible_text or "").lower()
    english_hints = (
        "captcha", "robot check", "access denied",
        "verify you are human", "unusual traffic",
    )
    for hint in english_hints:
        if hint in lowered:
            return True

    chinese_hints = (
        "访问异常", "安全验证", "滑动验证", "验证码", "请完成验证",
        "检测到异常", "系统检测到", "访问过于频繁", "请稍后再试",
        "请先登录", "登录后查看更多", "请登录后继续访问",
        "马上登录", "立即登录", "登录即可",
    )
    for hint in chinese_hints:
        if hint in (visible_text or ""):
            return True
    return False


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "header", "footer", "nav", "aside", "form", "button", "svg"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)
