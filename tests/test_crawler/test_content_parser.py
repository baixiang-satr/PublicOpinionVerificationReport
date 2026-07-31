import json

import pytest

from src.crawler.author_extractor import AuthorExtractor
from src.crawler.content_parser import ContentParser
from src.crawler.extractors.base import ImageCandidate, RenderedDocument
from src.crawler.extractors.generic import GenericExtractor
from src.crawler.platform_catalog import find_platform
from src.domain.models import ExtractionSource, PageData, RouteDecision


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


@pytest.mark.parametrize(
    "sheet_name",
    ("公众号", "图文视频", "生活资讯", "微博博客", "浏览器"),
)
def test_author_extractor_uses_nickname_for_missing_account_on_every_website(
    sheet_name: str,
) -> None:
    data = PageData(
        final_url="https://example.test/article/1",
        author_name="站点昵称",
    )

    AuthorExtractor().finalize(
        data,
        RouteDecision(sheet_name, "测试平台", "正文"),
    )

    assert data.author_id == "站点昵称"
    assert data.author_id_is_fallback is True
    assert data.field_sources["author_id"] == ExtractionSource.NICKNAME_FALLBACK
    assert data.field_confidences["author_id"] == 0.25


def test_author_extractor_never_replaces_a_real_account_with_nickname() -> None:
    data = PageData(author_name="昵称", author_id="real-account")

    AuthorExtractor().finalize(
        data,
        RouteDecision("生活资讯", "字节跳动_今日头条_生活资讯", "正文"),
    )

    assert data.author_id == "real-account"
    assert data.author_id_is_fallback is False


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


@pytest.mark.asyncio
async def test_douyin_parser_keeps_target_aweme_over_unscoped_config_node() -> None:
    """Regression for the two real short links reported on 2026-07-31."""

    target_id = "7667886625339225445"
    target = {
        "aweme_id": target_id,
        "desc": "道路千万条，安全第一条。",
        "create_time": 1_785_318_978,
        "author": {
            "nickname": "建柱种苗-李文亮",
            "uid": "1234567890123456789",
            "sec_uid": "SEC-TARGET",
        },
    }
    unrelated_config = {
        "title": "厂牌排名规则",
        "desc": "厂牌榜单值由旗下团员当日榜单值与榜单排名结果综合计算得出。",
    }

    class DouyinPage:
        url = f"https://www.douyin.com/video/{target_id}"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "抖音 - 视频",
                    "visibleText": "道路千万条，安全第一条。",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [unrelated_config],
                    "domValues": {},
                    "platformValues": {
                        "content_text": "道路千万条，安全第一条。",
                        "author_name": "建柱种苗-李文亮",
                        "published_at": "2026-07-29 17:56",
                    },
                    "images": [
                        {
                            "url": "https://cdn.example.test/recommendation.jpg",
                            "width": 640,
                            "height": 360,
                            "alt": "",
                            "context": "related-card",
                            "inContent": False,
                        }
                    ],
                }
            if "RENDER_DATA" in script:
                return json.dumps({"config": unrelated_config})
            return None

    definition = find_platform(DouyinPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        DouyinPage(),
        definition,
        network_payloads=({"aweme_detail": target},),
    )

    assert data.title == "道路千万条，安全第一条。"
    assert data.content_text == "道路千万条，安全第一条。"
    assert "厂牌" not in (data.content_text or "")
    assert data.author_name == "建柱种苗-李文亮"
    assert data.author_url == "https://www.douyin.com/user/SEC-TARGET"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-29 17:56:00"
    assert data.image_urls == []


@pytest.mark.asyncio
async def test_xiaohongshu_parser_keeps_url_matched_note_authoritative() -> None:
    target_id = "6a5dd45a0000000001033293"
    target = {
        "noteId": target_id,
        "title": "目标笔记标题",
        "desc": "目标正文#kimi[话题]#",
        "time": 1_784_534_106_000,
        "imageList": [
            {"urlDefault": "https://sns-img.example.test/target.webp"},
        ],
        "user": {
            "nickname": "目标作者",
            "userId": "target-user",
        },
    }
    unrelated = {
        "noteId": "6b0000000000000000000000",
        "title": "推荐笔记标题",
        "desc": "推荐卡片的正文不应进入结果",
        "time": 1_784_000_000_000,
        "user": {
            "nickname": "推荐作者",
            "userId": "other-user",
        },
    }

    class XiaohongshuPage:
        url = f"https://www.xiaohongshu.com/explore/{target_id}"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "目标笔记标题 - 小红书",
                    "visibleText": "目标正文",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [],
                    "domValues": {},
                    "platformValues": {},
                    "images": [
                        {
                            "url": "https://sns-img.example.test/recommendation.webp",
                            "width": 1080,
                            "height": 1440,
                            "alt": "",
                            "context": "recommend-card",
                            "inContent": True,
                        }
                    ],
                }
            if "window.__INITIAL_STATE__" in script:
                return target
            return None

    definition = find_platform(XiaohongshuPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        XiaohongshuPage(),
        definition,
        network_payloads=({"data": {"items": [{"note_card": unrelated}]}},),
    )

    assert data.title == "目标笔记标题"
    assert data.content_text == "目标正文#kimi"
    assert data.author_name == "目标作者"
    assert data.author_id == "target-user"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-20 15:55:06"
    )
    assert data.image_urls == [
        "https://sns-img.example.test/target.webp",
    ]
