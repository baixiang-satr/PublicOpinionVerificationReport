import json

import pytest

from src.crawler.author_extractor import AuthorExtractor
from src.crawler.content_parser import ContentParser
from src.crawler.extractors.base import ImageCandidate, RenderedDocument
from src.crawler.extractors.generic import GenericExtractor
from src.crawler.platform_catalog import find_platform
from src.domain.models import ExtractionSource, RouteDecision


def test_generic_extractor_prioritizes_json_ld_and_filters_images() -> None:
    document = RenderedDocument(
        url="https://news.example.test/a",
        title="DOM title",
        visible_text="Fallback body",
        meta={
            "og:title": "Meta title",
            "description": "Meta description",
            "author": "Meta author",
            "article:published_time": "2026-07-28T10:30:00+08:00",
        },
        json_ld=(
            json.dumps(
                {
                    "@type": "NewsArticle",
                    "headline": "Structured title",
                    "articleBody": "Line one\nLine one\nLine two",
                    "author": {"name": "Structured author", "identifier": "author-7", "url": "/people/7"},
                    "datePublished": "2026-07-28T09:00:00+08:00",
                    "image": ["https://cdn.example.test/cover.jpg"],
                }
            ),
        ),
        images=(
            ImageCandidate("https://cdn.example.test/body.jpg", 800, 600),
            ImageCandidate("https://cdn.example.test/logo.png", 300, 100, "site logo"),
            ImageCandidate("data:image/png;base64,abc", 800, 600),
        ),
    )

    data = GenericExtractor(summary_max_chars=12).extract(document)

    assert data.title == "Structured title"
    assert data.author_name == "Structured author"
    assert data.author_id == "author-7"
    assert data.content_text == "Line one\nLine two"
    assert data.summary_truncated
    assert data.field_sources["title"] == ExtractionSource.JSON_LD
    assert data.image_urls == [
        "https://cdn.example.test/cover.jpg",
        "https://cdn.example.test/body.jpg",
    ]
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-28 09:00:00"


class FakePage:
    url = "https://mp.weixin.qq.com/s/test"

    async def evaluate(self, _script: str, selectors: dict[str, tuple[str, ...]]) -> dict:
        assert "#activity-name" in selectors["title"]
        return {
            "url": self.url,
            "title": "Browser title",
            "visibleText": "Visible article",
            "canonicalUrl": self.url,
            "meta": {"description": "Meta fallback"},
            "jsonLd": [],
            "domValues": {},
            "platformValues": {
                "title": "公众号标题",
                "content_text": "公众号正文",
                "author_name": "示例公众号",
                "published_at": "2026年07月28日 11:22:33",
            },
            "images": [],
        }


@pytest.mark.asyncio
async def test_content_parser_prefers_platform_dom_and_marks_nickname_id_fallback() -> None:
    definition = find_platform(FakePage.url)
    assert definition is not None
    data = await ContentParser().extract(FakePage(), definition)
    route = RouteDecision(definition.sheet_name, definition.platform_value, "正文")

    AuthorExtractor(allow_nickname_as_id=True).finalize(data, route)

    assert data.title == "公众号标题"
    assert data.content_summary == "公众号正文"
    assert data.author_id == "示例公众号"
    assert data.author_id_is_fallback
    assert data.field_sources["title"] == ExtractionSource.PLATFORM_DOM
    assert data.field_sources["author_id"] == ExtractionSource.NICKNAME_FALLBACK
    assert data.published_at is not None


def test_author_extractor_resolves_relative_home_url() -> None:
    data = GenericExtractor().extract(
        RenderedDocument(
            url="https://news.example.test/article/1",
            title="Title",
            visible_text="Body",
            json_ld=(
                json.dumps(
                    {
                        "@type": "Article",
                        "author": {"name": "Author", "url": "/people/7"},
                    }
                ),
            ),
        )
    )

    AuthorExtractor().finalize(
        data,
        RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
    )

    assert data.author_url == "https://news.example.test/people/7"
