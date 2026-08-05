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
        ".bottom-actions",
        "[class*='bottom-actions']",
        ".screen-container",
        "[class*='content']",
        "main",
    ),
    "author_name": (
        ".author-operate-container .clickable-area",
        ".author-operate-container .author",
        "[class*='author-operate'] [class*='author']",
        "[class*='finder'] [class*='nickname']",
        "[class*='nickname']",
        "[class*='author'] [class*='name']",
    ),
    "author_id": ("[data-finder-username]", "[data-username]"),
    "author_url": ("[class*='nickname'] a", "[class*='avatar'] a"),
    "published_at": (
        ".feed-create-time-wrap",
        "[class*='create-time']",
        "time",
        "[class*='publish'] [class*='time']",
        "[class*='create'] [class*='time']",
    ),
}

WECHAT_VIDEO_INCLUDE_PATTERNS = (r"/video", r"/finder", r"/sph(?:/|$|\?)")
