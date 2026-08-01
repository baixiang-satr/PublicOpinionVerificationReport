from __future__ import annotations

from typing import Any

import pytest

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms.netease_news import (
    NeteaseNewsExtractor,
    netease_article_id,
)
from src.domain.models import ExtractionSource

URL = "https://www.163.com/news/article/L35RNISH000189FH.html"


class FakePage:
    def __init__(self, response: Any) -> None:
        self.response = response

    async def evaluate(self, _script: str) -> Any:
        return self.response


@pytest.mark.asyncio
async def test_netease_news_extracts_article_scoped_fields() -> None:
    probe = {
        "canonical": URL,
        "title": "测试新闻标题",
        "content": "第一段正文。\n第二段正文。",
        "author": "新华社客户端",
        "authorUrl": "https://h.xinhuaxmt.com/vh512/share/13222030",
        "published": "2026-07-31 12:36:27",
        "imageUrls": [
            "https://cms-bucket.example.test/article.jpg",
            "https://cms-bucket.example.test/article.jpg",
        ],
    }
    definition = find_platform(URL)
    assert definition is not None

    data = await NeteaseNewsExtractor().extract(
        FakePage(probe),
        RenderedDocument(url=URL),
        definition,
    )

    assert data is not None
    assert data.title == "测试新闻标题"
    assert data.content_text == "第一段正文。\n第二段正文。"
    assert data.author_name == "新华社客户端"
    # “本文来源” links to a syndicated source article, not the author's home.
    assert data.author_url is None
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-31 12:36:27"
    )
    assert data.image_urls == [
        "https://cms-bucket.example.test/article.jpg",
    ]
    assert data.field_sources["content_text"] == ExtractionSource.PLATFORM_DOM


@pytest.mark.asyncio
async def test_netease_news_rejects_another_article_canonical() -> None:
    definition = find_platform(URL)
    assert definition is not None
    probe = {
        "canonical": "https://www.163.com/news/article/AAAAAAAAAAAAAAAA.html",
        "title": "推荐文章",
        "content": "推荐文章正文",
    }

    data = await NeteaseNewsExtractor().extract(
        FakePage(probe),
        RenderedDocument(url=URL),
        definition,
    )

    assert data is None


def test_netease_article_id_supports_current_article_routes() -> None:
    assert netease_article_id(URL) == "L35RNISH000189FH"
    assert (
        netease_article_id(
            "https://www.163.com/dy/article/l35rnish000189fh.html"
        )
        == "L35RNISH000189FH"
    )
    assert netease_article_id("https://www.163.com/") is None
