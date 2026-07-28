"""Derive official author-home URLs only for stable, auditable URL patterns."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, urlsplit


def derive_author_profile_url(
    final_url: str | None,
    author_id: str | None,
) -> str | None:
    """Return an official profile URL when the platform pattern is unambiguous."""

    if not final_url:
        return None
    parsed = urlsplit(final_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path

    # The input itself can already be a personal-center page.
    if _is_direct_profile(host, path):
        return final_url

    if host == "mp.weixin.qq.com":
        biz = parse_qs(parsed.query).get("__biz", [""])[0].strip()
        if biz:
            return (
                "https://mp.weixin.qq.com/mp/profile_ext"
                f"?action=home&__biz={quote(biz, safe='=')}&scene=124#wechat_redirect"
            )

    identifier = (author_id or "").strip()
    if not identifier:
        return None
    encoded = quote(identifier, safe="")
    if host.endswith("douyin.com") and re.fullmatch(r"[A-Za-z0-9_-]{8,}", identifier):
        return f"https://www.douyin.com/user/{encoded}"
    if host.endswith("bilibili.com") and identifier.isdigit():
        return f"https://space.bilibili.com/{encoded}"
    if host.endswith("weibo.com") and identifier.isdigit():
        return f"https://weibo.com/u/{encoded}"
    if host.endswith("xiaohongshu.com") and re.fullmatch(
        r"[A-Za-z0-9_-]{8,}",
        identifier,
    ):
        return f"https://www.xiaohongshu.com/user/profile/{encoded}"
    if host.endswith("kuaishou.com") and re.fullmatch(
        r"[A-Za-z0-9_-]{6,}",
        identifier,
    ):
        return f"https://www.kuaishou.com/profile/{encoded}"
    if host.endswith("baidu.com") and identifier.isdigit():
        return f"https://author.baidu.com/home/{encoded}"
    if host.endswith("toutiao.com") and re.fullmatch(
        r"[A-Za-z0-9_-]{6,}",
        identifier,
    ):
        return f"https://www.toutiao.com/c/user/token/{encoded}/"
    if host.endswith("ixigua.com") and identifier.isdigit():
        return f"https://www.ixigua.com/home/{encoded}/"
    return None


def _is_direct_profile(host: str, path: str) -> bool:
    patterns = (
        ("sns.sohu.com", r"/(?:share/)?profile/[^/?#]+"),
        ("douyin.com", r"/user/[^/?#]+"),
        ("bilibili.com", r"/\d+/?$"),
        ("weibo.com", r"/u/\d+/?$"),
        ("xiaohongshu.com", r"/user/profile/[^/?#]+"),
        ("kuaishou.com", r"/profile/[^/?#]+"),
        ("zhihu.com", r"/people/[^/?#]+"),
        ("toutiao.com", r"/c/user/[^/?#]+/[^/?#]+"),
        ("ixigua.com", r"/home/\d+"),
    )
    return any(
        (host == domain or host.endswith(f".{domain}"))
        and re.search(pattern, path, re.I)
        for domain, pattern in patterns
    )
