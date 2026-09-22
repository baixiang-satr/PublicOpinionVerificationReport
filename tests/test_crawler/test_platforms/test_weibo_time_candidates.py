"""Weibo DOM-fallback multi-time-candidate selection (offline FakePage).

Split from ``test_extractors.py`` to keep every file under the 500-line
release-check limit.  微博详情页常含多个时间（主帖/被转发原帖/评论/推荐），
DOM 兜底应取距离现在最近的主帖区候选。
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.weibo import WeiboExtractor, _parse_weibo_time


class FakePage:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def evaluate(self, script: str) -> Any:
        for key, value in self._responses.items():
            if key in script:
                return value
        return None


def _definition() -> PlatformDefinition:
    return PlatformDefinition(
        "weibo",
        "图文视频",
        "测试_weibo",
        ExtractorFamily.SOCIAL,
        ("weibo.example.test",),
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_dom_probe_picks_latest_in_post_time_over_comment() -> None:
    probe = {
        "content": "DOM 正文",
        "author": "DOM 作者",
        "authorUrl": "https://weibo.com/u/42",
        "times": [
            {"text": "2024-05-01 08:00", "inComment": False},
            {"text": "2026-09-21 12:30", "inComment": True},
            {"text": "2026-09-21 10:00", "inComment": False},
        ],
    }
    page = FakePage({"detail_wbtext": probe})

    data = _run(
        WeiboExtractor().extract(page, RenderedDocument(url="https://weibo.com/1/2"), _definition())
    )

    assert data is not None
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M") == "2026-09-21 10:00"


def test_dom_probe_falls_back_to_comment_time_when_it_is_the_only_candidate() -> None:
    probe = {
        "content": "DOM 正文",
        "author": "DOM 作者",
        "times": [{"text": "2026-09-20 18:05", "inComment": True}],
    }
    page = FakePage({"detail_wbtext": probe})

    data = _run(
        WeiboExtractor().extract(page, RenderedDocument(url="https://weibo.com/1/2"), _definition())
    )

    assert data is not None
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M") == "2026-09-20 18:05"


def test_dom_probe_supports_legacy_single_time_key() -> None:
    probe = {"content": "DOM 正文", "author": "DOM 作者", "time": "2025-07-01 08:00"}
    page = FakePage({"detail_wbtext": probe})

    data = _run(
        WeiboExtractor().extract(page, RenderedDocument(url="https://weibo.com/1/2"), _definition())
    )

    assert data is not None
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M") == "2025-07-01 08:00"


def test_parse_weibo_time_supports_chinese_and_relative_formats() -> None:
    parsed = _parse_weibo_time("9月21日 10:00")
    assert parsed is not None
    assert (parsed.month, parsed.day, parsed.hour, parsed.minute) == (9, 21, 10, 0)
    assert _parse_weibo_time("今天 08:30") is not None
    assert _parse_weibo_time("1小时前") is not None
    assert _parse_weibo_time("不是时间") is None
