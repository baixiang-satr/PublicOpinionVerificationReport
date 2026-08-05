from __future__ import annotations

import asyncio
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms.wechat import WechatExtractor
from src.domain.models import ExtractionSource

OFFICIAL_URL = "https://mp.weixin.qq.com/s/Oxxb7Lc4zEUsbbYXoOtXNg"
VIDEO_URL = "https://weixin.qq.com/sph/AiQbKWmgTm"


class FakePage:
    def __init__(self, probe: dict[str, Any]) -> None:
        self._probe = probe

    async def evaluate(self, _script: str) -> dict[str, Any]:
        return self._probe


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_wechat_official_extracts_article_dom_and_page_globals() -> None:
    definition = find_platform(OFFICIAL_URL)
    assert definition is not None
    probe = {
        "official": {
            "title": "公众号文章标题",
            "content": "第一段\n第二段",
            "author": "示例公众号",
            "authorId": "gh_example",
            "accountUin": "123456789",
            "published": "1753956000",
        },
        "video": {},
    }

    data = _run(
        WechatExtractor().extract(
            FakePage(probe),
            RenderedDocument(url=OFFICIAL_URL),
            definition,
        )
    )

    assert data is not None
    assert data.title == "公众号文章标题"
    assert data.content_text == "第一段\n第二段"
    assert data.author_name == "示例公众号"
    assert data.author_id == "gh_example"
    assert data.account_uin == "123456789"
    assert data.published_at is not None
    assert data.field_sources["content_text"] == ExtractionSource.PLATFORM_DOM


def test_wechat_video_extracts_finder_object_payload() -> None:
    definition = find_platform(VIDEO_URL)
    assert definition is not None
    payload = {
        "data": {
            "finderObject": {
                "objectDesc": "视频号测试文案",
                "createTime": 1_753_956_000,
                "finderUser": {
                    "nickname": "视频号作者",
                    "username": "finder_user_123",
                },
            }
        }
    }
    document = RenderedDocument(
        url=VIDEO_URL,
        network_payloads=(payload,),
    )

    data = _run(
        WechatExtractor().extract(
            FakePage({"official": {}, "video": {}}),
            document,
            definition,
        )
    )

    assert data is not None
    assert data.title == "视频号测试文案"
    assert data.content_text == "视频号测试文案"
    assert data.author_name == "视频号作者"
    assert data.author_id == "finder_user_123"
    assert data.published_at is not None
    assert data.field_sources["content_text"] == ExtractionSource.NETWORK_JSON


def test_wechat_video_uses_dom_and_open_graph_fallbacks() -> None:
    definition = find_platform(VIDEO_URL)
    assert definition is not None
    document = RenderedDocument(
        url=VIDEO_URL,
        meta={"og:title": "视频号标题", "og:description": "视频号简介"},
    )
    probe = {
        "official": {},
        "video": {
            "author": "DOM 作者",
            "authorId": "dom_author",
            "published": "2026-07-31 10:00",
        },
    }

    data = _run(
        WechatExtractor().extract(FakePage(probe), document, definition)
    )

    assert data is not None
    assert data.title == "视频号标题"
    assert data.content_text == "视频号简介"
    assert data.author_name == "DOM 作者"
    assert data.author_id == "dom_author"
    assert data.field_sources["content_text"] == ExtractionSource.META


def test_wechat_video_reads_feed_info_api_payload() -> None:
    """finder-preview get_feed_info 接口：feedInfo+authorInfo 同层结构。"""

    definition = find_platform(VIDEO_URL)
    assert definition is not None
    payload = {
        "data": {
            "authorInfo": {"nickname": "戴华明札记", "headImgUrl": "https://wx.qlogo.cn/x"},
            "feedInfo": {
                "description": "新宁县税务局在信访答复文末额外增加警示条款……",
                "createtime": 1_782_257_803,
            },
        },
        "errCode": 0,
    }
    document = RenderedDocument(url=VIDEO_URL, network_payloads=(payload,))

    data = _run(
        WechatExtractor().extract(
            FakePage({"official": {}, "video": {}}), document, definition
        )
    )

    assert data is not None
    assert data.content_text == "新宁县税务局在信访答复文末额外增加警示条款……"
    assert data.author_name == "戴华明札记"
    assert data.published_at is not None
    assert data.field_sources["content_text"] == ExtractionSource.NETWORK_JSON


def test_given_wechat_urls_route_to_implemented_crawlers() -> None:
    official = find_platform(OFFICIAL_URL)
    video = find_platform(VIDEO_URL)

    assert official is not None and official.key == "wechat_official"
    assert video is not None and video.key == "wechat_video"
    assert not official.manual_only
    assert not video.manual_only


class _ScriptAwarePage:
    """按脚本内容分别应答：页面全局探测 vs DOM 探针。"""

    def __init__(self, globals_payload: object, probe: dict[str, Any]) -> None:
        self._globals_payload = globals_payload
        self._probe = probe

    async def evaluate(self, script: str) -> Any:
        if "__feedInfo__" in script:
            return self._globals_payload
        return self._probe


def test_wechat_video_reads_page_globals_feed_info() -> None:
    definition = find_platform(VIDEO_URL)
    assert definition is not None
    feed_info = {
        "feed": {
            "objectDesc": "全局对象里的视频号文案",
            "createTime": 1_785_800_000,
            "finderUser": {"nickname": "全局作者", "username": "global_finder"},
        }
    }
    import json

    page = _ScriptAwarePage(json.dumps(feed_info), {"official": {}, "video": {}})

    data = _run(WechatExtractor().extract(page, RenderedDocument(url=VIDEO_URL), definition))

    assert data is not None
    assert data.content_text == "全局对象里的视频号文案"
    assert data.author_name == "全局作者"
    assert data.author_id == "global_finder"
    assert data.published_at is not None


def test_wechat_video_rejects_shell_boilerplate_meta() -> None:
    definition = find_platform(VIDEO_URL)
    assert definition is not None
    document = RenderedDocument(
        url=VIDEO_URL,
        meta={"og:title": "微信视频号", "og:description": "微信视频号"},
    )
    page = _ScriptAwarePage(None, {"official": {}, "video": {}})

    assert _run(WechatExtractor().extract(page, document, definition)) is None
