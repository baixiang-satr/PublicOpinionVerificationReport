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


def test_generic_extractor_reads_framework_hydration_before_visible_text() -> None:
    document = RenderedDocument(
        url="https://www.kuaishou.com/short-video/example",
        visible_text="页面外壳",
        embedded_payloads=(
            {
                "props": {
                    "pageProps": {
                        "photo": {
                            "title": "内嵌标题",
                            "caption": "来自 hydration 的正文",
                            "author": {
                                "name": "内嵌作者",
                                "id": "author-88",
                                "url": "/profile/author-88",
                            },
                            "publishTime": "2026-07-28T12:00:00+08:00",
                        }
                    }
                }
            },
        ),
    )

    data = GenericExtractor().extract(document)

    assert data.title == "内嵌标题"
    assert data.content_text == "来自 hydration 的正文"
    assert data.author_name == "内嵌作者"
    assert data.author_id == "author-88"
    assert data.field_sources["content_text"] == ExtractionSource.EMBEDDED_JSON


def test_generic_extractor_uses_network_json_before_visible_text() -> None:
    document = RenderedDocument(
        url="https://example.test/article/1",
        visible_text="网页正在加载",
        network_payloads=(
            {
                "data": {
                    "headline": "接口标题",
                    "articleBody": "接口正文",
                    "authorName": "接口作者",
                }
            },
        ),
    )

    data = GenericExtractor().extract(document)

    assert data.title == "接口标题"
    assert data.content_text == "接口正文"
    assert data.field_sources["title"] == ExtractionSource.NETWORK_JSON


def test_generic_extractor_reads_common_social_api_field_variants() -> None:
    document = RenderedDocument(
        url="https://weibo.com/5644764907/example",
        visible_text="页面外壳",
        network_payloads=(
            {
                "status": {
                    "itemTitle": "接口中的帖子标题",
                    "text_raw": "接口中的完整帖子正文",
                    "pubdate": 1785204000,
                    "user": {
                        "screen_name": "接口昵称",
                        "id_str": "5644764907",
                        "homepage": "https://weibo.com/u/5644764907",
                    },
                }
            },
        ),
    )

    data = GenericExtractor().extract(document)

    assert data.title == "接口中的帖子标题"
    assert data.content_text == "接口中的完整帖子正文"
    assert data.author_name == "接口昵称"
    assert data.author_id == "5644764907"
    assert data.author_url == "https://weibo.com/u/5644764907"
    assert data.published_at is not None


def test_generic_extractor_rejects_navigation_chrome_as_author_name() -> None:
    data = GenericExtractor().extract(
        RenderedDocument(
            url="https://example.test/video/1",
            title="视频标题",
            visible_text="视频正文",
            dom_values={"author_name": "首页"},
        )
    )

    assert data.author_name is None


def test_generic_extractor_keeps_only_name_from_author_container() -> None:
    data = GenericExtractor().extract(
        RenderedDocument(
            url="https://example.test/post/1",
            title="帖子标题",
            visible_text="帖子正文",
            dom_values={"author_name": "可获得我\n2025-08-05"},
        )
    )

    assert data.author_name == "可获得我"


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


@pytest.mark.asyncio
async def test_content_parser_does_not_guess_publish_date_from_url() -> None:
    class DatePathPage(FakePage):
        url = "https://news.example.test/article/2026/07/28/story"

        async def evaluate(self, _script: str, _selectors: object) -> dict:
            return {
                "url": self.url,
                "title": "Date path article",
                "visibleText": "Body",
                "canonicalUrl": self.url,
                "meta": {},
                "jsonLd": [],
                "domValues": {},
                "platformValues": {},
                "images": [],
            }

    definition = find_platform("https://www.sohu.com/a/123")
    assert definition is not None
    data = await ContentParser().extract(DatePathPage(), definition)

    assert data.published_at_raw is None
    assert data.published_at is None


@pytest.mark.asyncio
async def test_commerce_parser_falls_back_from_author_to_store_name() -> None:
    class CommercePage(FakePage):
        url = "https://www.goofish.com/item?id=1"

        async def evaluate(self, _script: str, _selectors: object) -> dict:
            return {
                "url": self.url,
                "title": "闲鱼商品",
                "visibleText": "商品描述",
                "canonicalUrl": self.url,
                "meta": {},
                "jsonLd": [],
                "domValues": {"author_name": "闲鱼卖家"},
                "platformValues": {},
                "images": [],
            }

    definition = find_platform(CommercePage.url)
    assert definition is not None

    data = await ContentParser().extract(CommercePage(), definition)

    assert data.store_name == "闲鱼卖家"
    assert data.field_sources["store_name"] == ExtractionSource.GENERIC_DOM
