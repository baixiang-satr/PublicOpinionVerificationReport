"""Offline fixture tests for the additive API-assist extractor fallbacks.

Split from ``test_extractors.py`` to respect the release-check 500-line
file cap.  ``FakeApiPage`` extends the canned-script fake with a browser
request context so ``api_assist`` helpers can be exercised offline.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.douyin import DouyinExtractor
from src.crawler.platforms.kuaishou import KuaishouExtractor
from src.crawler.platforms.weibo import WeiboExtractor
from src.crawler.platforms.xiaohongshu import XiaohongshuExtractor
from src.crawler.platforms.zhihu import ZhihuExtractor


class FakePage:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def evaluate(self, script: str) -> Any:
        for key, value in self._responses.items():
            if key in script:
                return value
        return None


class _FakeApiResponse:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self._payload = payload

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    async def dispose(self) -> None:
        return None


class _FakeApiRequest:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def get(self, url: str, **kwargs: Any) -> _FakeApiResponse:
        for key, value in self._responses.items():
            if key in url:
                return value if isinstance(value, _FakeApiResponse) else _FakeApiResponse(200, value)
        return _FakeApiResponse(status=404)


class _FakeApiContext:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.request = _FakeApiRequest(responses)


class FakeApiPage(FakePage):
    """FakePage that also owns a browser request context for api_assist."""

    def __init__(self, responses: dict[str, Any], api: dict[str, Any]) -> None:
        super().__init__(responses)
        self.context = _FakeApiContext(api)


def _definition(key: str) -> PlatformDefinition:
    return PlatformDefinition(
        key,
        "图文视频",
        f"测试_{key}",
        ExtractorFamily.SOCIAL,
        (f"{key}.example.test",),
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_douyin_api_fallback_when_render_empty() -> None:
    item = {
        "desc": "API 视频文案",
        "create_time": 1_751_000_000,
        "author": {"nickname": "API 作者", "unique_id": "api001", "sec_uid": "SEC9"},
    }
    page = FakeApiPage({}, {"iteminfo": {"item_list": [item]}})
    document = RenderedDocument(url="https://www.douyin.com/video/7412571429857668386")

    data = _run(DouyinExtractor().extract(page, document, _definition("douyin")))

    assert data is not None
    assert data.content_text == "API 视频文案"
    assert data.author_name == "API 作者"
    assert data.author_url == "https://www.douyin.com/user/SEC9"
    assert data.field_sources["content_text"].value == "network_json"


def test_douyin_api_fallback_keeps_none_when_api_fails() -> None:
    page = FakeApiPage({}, {})
    document = RenderedDocument(url="https://www.douyin.com/video/7412571429857668386")
    assert _run(DouyinExtractor().extract(page, document, _definition("douyin"))) is None


def test_weibo_api_fallback_when_login_wall() -> None:
    mblog = {
        "text_raw": "移动端 API 正文",
        "created_at": "Wed Jul 01 12:00:00 +0800 2026",
        "user": {"screen_name": "API 博主", "idstr": "777"},
    }
    page = FakeApiPage({}, {"statuses/show": mblog})
    document = RenderedDocument(url="https://weibo.com/1542630033/Oj6V1vX2q")

    data = _run(WeiboExtractor().extract(page, document, _definition("weibo")))

    assert data is not None
    assert data.content_text == "移动端 API 正文"
    assert data.author_name == "API 博主"
    assert data.author_url == "https://weibo.com/u/777"
    assert data.field_sources["content_text"].value == "network_json"


def test_zhihu_api_fallback_for_blocked_question() -> None:
    answer = {
        "content": "<p>API 回答正文</p>",
        "created_time": 1_751_000_000,
        "author": {"name": "API 答主", "url_token": "api-da-zhu"},
        "question": {"title": "API 问题标题"},
    }
    page = FakeApiPage({}, {"api/v4/answers": answer})
    document = RenderedDocument(
        url="https://www.zhihu.com/question/19550225/answer/2757167088"
    )

    data = _run(ZhihuExtractor().extract(page, document, _definition("zhihu")))

    assert data is not None
    assert data.title == "API 问题标题"
    assert data.content_text == "API 回答正文"
    assert data.author_name == "API 答主"
    assert data.author_url == "https://www.zhihu.com/people/api-da-zhu"
    assert data.field_sources["content_text"].value == "network_json"


def test_xiaohongshu_author_dom_fallback() -> None:
    state = {
        "note": {
            "noteDetailMap": {
                "abc123": {
                    "note": {"title": "笔记标题", "desc": "笔记正文", "time": 1_751_000_000_000}
                }
            }
        }
    }
    probe = {"author": "DOM 红薯", "authorUrl": "https://www.xiaohongshu.com/user/profile/u99"}
    page = FakePage({"author-container": probe})
    document = RenderedDocument(
        url="https://www.xiaohongshu.com/explore/abc123",
        embedded_payloads=(state,),
    )

    data = _run(XiaohongshuExtractor().extract(page, document, _definition("xiaohongshu")))

    assert data is not None
    assert data.content_text == "笔记正文"
    assert data.author_name == "DOM 红薯"
    assert data.author_id == "u99"
    assert data.field_sources["author_name"].value == "platform_dom"


def test_kuaishou_photo_with_nested_user_node() -> None:
    body = {
        "data": {
            "photo": {
                "desc": "嵌套用户视频文案",
                "createTime": 1_751_000_000_000,
                "user": {"user_name": "嵌套作者", "user_id": "ks100"},
            }
        }
    }
    document = RenderedDocument(
        url="https://www.kuaishou.com/short-video/xyz",
        embedded_payloads=(body,),
    )

    data = _run(KuaishouExtractor().extract(FakePage({}), document, _definition("kuaishou")))

    assert data is not None
    assert data.content_text == "嵌套用户视频文案"
    assert data.author_name == "嵌套作者"
    assert data.author_url == "https://www.kuaishou.com/profile/ks100"
