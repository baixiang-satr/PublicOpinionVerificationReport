"""Catalog constants for the WeChat Channels share surface."""
from __future__ import annotations

from collections.abc import Mapping

WECHAT_VIDEO_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "title": (
        "[class*='finder'] [class*='title']",
        "[class*='video'] [class*='title']",
        "h1",
    ),
    "content_text": (
        "[class*='finder'] [class*='desc']",
        "[class*='video'] [class*='desc']",
        "[class*='content']",
        "main",
    ),
    "author_name": (
        "[class*='finder'] [class*='nickname']",
        "[class*='nickname']",
        "[class*='author'] [class*='name']",
    ),
    "author_id": ("[data-finder-username]", "[data-username]"),
    "author_url": ("[class*='nickname'] a", "[class*='avatar'] a"),
    "published_at": (
        "time",
        "[class*='publish'] [class*='time']",
        "[class*='create'] [class*='time']",
    ),
}

WECHAT_VIDEO_INCLUDE_PATTERNS = (r"/video", r"/finder", r"/sph(?:/|$|\?)")
