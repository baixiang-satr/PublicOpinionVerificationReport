"""Offline unit tests for the additive official-API fallback layer.

No real network: FakeRequest/FakeContext emulate ``page.context.request``
with canned responses keyed by URL substring.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.crawler import api_assist
from src.crawler.api_assist import (
    api_assist_enabled,
    douyin_aweme_detail,
    douyin_aweme_id,
    fetch_json,
    weibo_bid,
    weibo_mblog,
    zhihu_answer,
    zhihu_ids,
    zhihu_question,
)


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body

    async def dispose(self) -> None:
        return None


class FakeRequest:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requested.append(url)
        for key, response in self._responses.items():
            if key in url:
                return response
        return FakeResponse(status=404)


class FakeContext:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.request = FakeRequest(responses)


class FakePage:
    def __init__(self, responses: dict[str, FakeResponse] | None = None) -> None:
        self.context = FakeContext(responses or {})


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _json_response(payload: Any, status: int = 200) -> FakeResponse:
    return FakeResponse(status=status, body=json.dumps(payload).encode("utf-8"))


# ── URL parsers ──


def test_douyin_aweme_id_parses_video_and_note_urls() -> None:
    assert douyin_aweme_id("https://www.douyin.com/video/7412571429857668386") == "7412571429857668386"
    assert douyin_aweme_id("https://www.douyin.com/note/7412571429857668386?x=1") == "7412571429857668386"
    assert douyin_aweme_id("https://www.douyin.com/user/SEC123") is None


def test_weibo_bid_parses_profile_and_detail_urls() -> None:
    assert weibo_bid("https://weibo.com/5644764907/5266894313755145") == "5266894313755145"
    assert weibo_bid("https://weibo.com/1542630033/Oj6V1vX2q") == "Oj6V1vX2q"
    assert weibo_bid("https://m.weibo.cn/detail/Oj6V1vX2q") == "Oj6V1vX2q"
    assert weibo_bid("https://weibo.com/") is None


def test_zhihu_ids_parses_question_and_answer() -> None:
    assert zhihu_ids("https://www.zhihu.com/question/19550225/answer/2757167088") == (
        "19550225",
        "2757167088",
    )
    assert zhihu_ids("https://www.zhihu.com/question/19550225") == ("19550225", None)
    assert zhihu_ids("https://zhuanlan.zhihu.com/p/102280558") == (None, None)


# ── fetch_json transport ──


def test_fetch_json_returns_parsed_payload() -> None:
    page = FakePage({"https://example.com/api": _json_response({"ok": 1})})
    assert _run(fetch_json(page, "https://example.com/api")) == {"ok": 1}


def test_fetch_json_returns_none_on_http_error() -> None:
    page = FakePage({"https://example.com/api": _json_response({}, status=403)})
    assert _run(fetch_json(page, "https://example.com/api")) is None


def test_fetch_json_returns_none_without_browser_context() -> None:
    class BarePage:
        pass

    assert _run(fetch_json(BarePage(), "https://example.com/api")) is None


def test_fetch_json_honours_disable_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POR_DISABLE_API_ASSIST", "1")
    assert not api_assist_enabled()
    page = FakePage({"https://example.com/api": _json_response({"ok": 1})})
    assert _run(fetch_json(page, "https://example.com/api")) is None
    assert page.context.request.requested == []


# ── douyin ──


def test_douyin_aweme_detail_from_iteminfo_item_list() -> None:
    item = {"desc": "视频文案", "create_time": 1_751_000_000, "author": {"nickname": "作者"}}
    page = FakePage({"iteminfo": _json_response({"item_list": [item]})})

    detail = _run(douyin_aweme_detail(page, "7412571429857668386"))

    assert detail is not None
    assert detail["desc"] == "视频文案"
    assert page.context.request.requested[0].startswith(
        "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"
    )


def test_douyin_aweme_detail_falls_back_to_share_router_data() -> None:
    item = {"desc": "分享页文案", "author": {"nickname": "作者B"}}
    router_data = {
        "loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [item]}}}
    }
    page = FakePage(
        {
            "iteminfo": FakeResponse(status=200, body=b"not json"),
            "share/video": _json_response(router_data),
        }
    )

    detail = _run(douyin_aweme_detail(page, "7412571429857668386"))

    assert detail is not None
    assert detail["desc"] == "分享页文案"


def test_douyin_aweme_detail_returns_none_when_both_fail() -> None:
    page = FakePage({})
    assert _run(douyin_aweme_detail(page, "7412571429857668386")) is None


# ── weibo ──


def test_weibo_mblog_accepts_direct_mblog_shape() -> None:
    mblog = {"text_raw": "正文", "created_at": "Wed Jul 01 12:00:00 +0800 2026",
             "user": {"screen_name": "博主", "idstr": "123"}}
    page = FakePage({"statuses/show": _json_response(mblog)})

    assert _run(weibo_mblog(page, "Oj6V1vX2q")) == mblog


def test_weibo_mblog_accepts_data_wrapped_shape() -> None:
    mblog = {"text": "<p>正文</p>", "user": {"screen_name": "博主"}}
    page = FakePage({"statuses/show": _json_response({"ok": 1, "data": mblog})})

    assert _run(weibo_mblog(page, "Oj6V1vX2q")) == mblog


def test_weibo_mblog_returns_none_on_login_redirect_payload() -> None:
    page = FakePage({"statuses/show": _json_response({"ok": 0, "msg": "login"})})
    assert _run(weibo_mblog(page, "Oj6V1vX2q")) is None


# ── zhihu ──


def test_zhihu_answer_returns_mapping_with_content() -> None:
    answer = {"content": "<p>回答</p>", "created_time": 1_751_000_000,
              "author": {"name": "答主", "url_token": "da-zhu"},
              "question": {"title": "问题标题"}}
    page = FakePage({"api/v4/answers": _json_response(answer)})

    assert _run(zhihu_answer(page, "2757167088")) == answer


def test_zhihu_question_returns_mapping_with_title() -> None:
    question = {"title": "问题标题", "detail": "<p>补充</p>", "created": 1_751_000_000,
                "author": {"name": "题主"}}
    page = FakePage({"api/v4/questions": _json_response(question)})

    assert _run(zhihu_question(page, "19550225")) == question


def test_zhihu_answer_returns_none_on_error_payload() -> None:
    page = FakePage({"api/v4/answers": _json_response({"error": {"code": 403}})})
    assert _run(zhihu_answer(page, "2757167088")) is None
