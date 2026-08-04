"""Bounded pre-resolution of known share short-links before page navigation.

Short links (``v.douyin.com/...``, ``xhslink.com/...``, ``b23.tv/...`` …)
hide the real content URL behind one or more HTTP redirects.  Navigating
them blind costs a full render cycle per hop and — worse for Douyin — never
exposes the numeric aweme id the API-assist fallback needs.  This module
resolves the redirect chain through the browser context's request API
(shared cookies, bounded 10s, no rendering) and canonicalizes known
platform URL shapes, so the crawler navigates the stable content URL
directly.  Everything is read-only and never raises: any failure yields
``None`` and the caller falls back to navigating the original short link.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_RESOLVE_TIMEOUT_MS = 10_000
_MAX_REDIRECTS = 10

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Hosts that exist purely to redirect to a real content page.
_SHORT_LINK_HOSTS = frozenset(
    {
        "v.douyin.com",
        "xhslink.com",
        "b23.tv",
        "t.cn",
        "v.kuaishou.com",
        "url.cn",
        "dwz.cn",
        "m.toutiao.com",
        "m.ixigua.com",
    }
)

_DOUYIN_SHARE_RE = re.compile(
    r"(?:iesdouyin\.com/share/(video|note)/|douyin\.com/(video|note)/)(\d{6,})"
)

# m.ixigua.com/dx/{group_id} share pages and www.ixigua.com/video/{group_id}
# both expose the numeric group id directly in the path.
_IXIGUA_SHARE_RE = re.compile(r"ixigua\.com/(?:dx|video)/(\d{10,})")


def is_short_link(url: str) -> bool:
    """Whether *url* sits on a known redirect-only short-link host."""

    host = (urlsplit(url).hostname or "").casefold()
    return host in _SHORT_LINK_HOSTS


async def resolve_share_link(page: Any, url: str) -> str | None:
    """Return the canonical content URL for a known short link, else ``None``.

    Douyin share URLs are canonicalized to ``www.douyin.com/video|note/{id}``
    so the aweme id is visible to the extractor and API-assist fallback; for
    every other shortener the final redirect target is returned unchanged.
    """

    if not is_short_link(url):
        return None
    final_url = await _final_url(page, url)
    if not final_url:
        # No HTTP redirect followed (JS-redirect share page or failure):
        # ixigua /dx/ links still reveal the group id in the original path.
        return _canonical_ixigua(url)
    canonical = _canonical_douyin(final_url)
    if canonical is not None:
        return canonical
    canonical = _canonical_ixigua(final_url)
    if canonical is not None:
        return canonical
    final_host = (urlsplit(final_url).hostname or "").casefold()
    if final_host and final_host not in _SHORT_LINK_HOSTS:
        return final_url
    return _canonical_ixigua(url)


def _canonical_douyin(url: str) -> str | None:
    match = _DOUYIN_SHARE_RE.search(url)
    if match is None:
        return None
    kind = match.group(1) or match.group(2)
    aweme_id = match.group(3)
    return f"https://www.douyin.com/{kind}/{aweme_id}"


def _canonical_ixigua(url: str) -> str | None:
    """Return the stable desktop video URL for an ixigua share/video URL."""

    match = _IXIGUA_SHARE_RE.search(url)
    if match is None:
        return None
    return f"https://www.ixigua.com/video/{match.group(1)}"


async def _final_url(page: Any, url: str) -> str | None:
    """GET *url* through the page's request context and return the final URL."""

    context = getattr(page, "context", None)
    request = getattr(context, "request", None)
    get = getattr(request, "get", None)
    if get is None:
        return None
    response = None
    try:
        response = await get(
            url,
            headers={
                "User-Agent": _DESKTOP_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=_RESOLVE_TIMEOUT_MS,
            max_redirects=_MAX_REDIRECTS,
            fail_on_status_code=False,
        )
        status = int(getattr(response, "status", 0) or 0)
        if status >= 400:
            logger.debug("share link %s -> HTTP %s", url, status)
            return None
        final = str(getattr(response, "url", "") or "")
        return final or None
    except Exception as error:
        logger.debug("share link resolution failed for %s: %s", url, error)
        return None
    finally:
        if response is not None:
            try:
                await response.dispose()
            except Exception:
                pass
