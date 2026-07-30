"""Offline tests for share short-link pre-resolution (no real network)."""
from __future__ import annotations

import asyncio
from typing import Any

from src.crawler.share_links import is_short_link, resolve_share_link


class FakeResponse:
    def __init__(self, status: int = 200, url: str = "") -> None:
        self.status = status
        self.url = url

    async def dispose(self) -> None:
        return None


class FakeRequest:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self._response = response
        self.requested: list[str] = []

    async def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.requested.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeContext:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.request = FakeRequest(response)


class FakePage:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.context = FakeContext(response)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_is_short_link_matches_known_hosts_only() -> None:
    assert is_short_link("https://v.douyin.com/erOICsACek8/")
    assert is_short_link("https://xhslink.com/a/abc")
    assert is_short_link("https://b23.tv/abc")
    assert not is_short_link("https://www.douyin.com/video/123")
    assert not is_short_link("https://example.test/x")


def test_resolve_douyin_share_link_canonicalizes_to_video_url() -> None:
    page = FakePage(
        FakeResponse(
            url="https://www.iesdouyin.com/share/video/7557112345678901234/?region=CN"
        )
    )
    resolved = _run(resolve_share_link(page, "https://v.douyin.com/erOICsACek8/"))
    assert resolved == "https://www.douyin.com/video/7557112345678901234"


def test_resolve_douyin_note_share_link_keeps_note_kind() -> None:
    page = FakePage(
        FakeResponse(url="https://www.iesdouyin.com/share/note/7557112345678901234/")
    )
    resolved = _run(resolve_share_link(page, "https://v.douyin.com/abcDef123/"))
    assert resolved == "https://www.douyin.com/note/7557112345678901234"


def test_resolve_douyin_direct_url_canonicalizes_too() -> None:
    page = FakePage(
        FakeResponse(url="https://www.douyin.com/video/7557112345678901234?previous_page=1")
    )
    resolved = _run(resolve_share_link(page, "https://v.douyin.com/erOICsACek8/"))
    assert resolved == "https://www.douyin.com/video/7557112345678901234"


def test_resolve_non_douyin_short_link_returns_final_url() -> None:
    final = "https://www.xiaohongshu.com/discovery/item/64ab?xsec=1"
    page = FakePage(FakeResponse(url=final))
    assert _run(resolve_share_link(page, "https://xhslink.com/a/xyz")) == final


def test_resolve_skips_non_short_link_without_http() -> None:
    page = FakePage(FakeResponse(url="https://www.douyin.com/video/123"))
    assert _run(resolve_share_link(page, "https://www.douyin.com/video/123")) is None
    assert page.context.request.requested == []


def test_resolve_returns_none_on_http_error() -> None:
    page = FakePage(
        FakeResponse(status=404, url="https://www.iesdouyin.com/share/video/1/")
    )
    assert _run(resolve_share_link(page, "https://v.douyin.com/x/")) is None


def test_resolve_returns_none_on_transport_failure() -> None:
    page = FakePage(TimeoutError("boom"))
    assert _run(resolve_share_link(page, "https://v.douyin.com/x/")) is None


def test_resolve_returns_none_when_redirect_stays_on_shortener() -> None:
    page = FakePage(FakeResponse(url="https://v.douyin.com/other/"))
    assert _run(resolve_share_link(page, "https://v.douyin.com/x/")) is None
