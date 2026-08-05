"""23 条验收分享链接的平台路由回归测试（离线，无网络）。

链接清单与 ``tests/test_input/social_share_links.csv`` 行序一致；
验收工具 ``tools/test_share_links.py`` 使用同一预期矩阵。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.crawler.api_assist import douyin_aweme_id
from src.crawler.platform_catalog import find_platform
from src.crawler.share_links import canonicalize_share_url, is_short_link, resolve_share_link

_CSV = Path(__file__).resolve().parents[1] / "test_input" / "social_share_links.csv"

EXPECTED_PLATFORMS: tuple[str, ...] = (
    "wechat_official",
    "wechat_video",
    "wechat_video",
    "xiaohongshu",
    "xiaohongshu",
    "xiaohongshu",
    "weibo",
    "weibo",
    "weibo",
    "douyin",
    "douyin",
    "douyin",
    "toutiao",
    "toutiao",
    "toutiao",
    "bilibili",
    "bilibili",
    "bilibili",
    "ixigua",
    "ixigua",
    "ixigua",
    "baijiahao",
    "baijiahao",
)


def _csv_urls() -> list[str]:
    return [line.strip() for line in _CSV.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_acceptance_links_route_to_expected_platform() -> None:
    urls = _csv_urls()
    assert len(urls) == len(EXPECTED_PLATFORMS) == 23
    misses: list[str] = []
    for url, expected in zip(urls, EXPECTED_PLATFORMS, strict=True):
        detected = find_platform(url)
        if detected is None or detected.key != expected:
            misses.append(f"{expected} <- {url[:80]} (got {detected.key if detected else None})")
    assert not misses, "路由未命中预期平台:\n" + "\n".join(misses)


def test_douyin_user_modal_url_yields_aweme_id() -> None:
    url = (
        "https://www.douyin.com/user/MS4wLjABAAAAUMAd-PhEWYOgVbIgyw_GIo7nGfd8rSXSixdDN-JdG9L"
        "yuPv_EQCIqExT3shffH6C?from_tab_name=main&modal_id=7668445095837846799&vid=7669740629890518313"
    )
    assert douyin_aweme_id(url) == "7668445095837846799"


def test_douyin_video_url_still_yields_aweme_id() -> None:
    assert douyin_aweme_id("https://www.douyin.com/video/7557112345678901234") == "7557112345678901234"
    assert douyin_aweme_id("https://www.douyin.com/note/7557112345678901234?x=1") == "7557112345678901234"
    assert douyin_aweme_id("https://www.douyin.com/discover") is None


def test_douyin_user_modal_url_canonicalizes_to_video_page() -> None:
    url = (
        "https://www.douyin.com/user/MS4wLjABAAAAUMAd-PhEWYOgVbIgyw"
        "?from_tab_name=main&modal_id=7669738019598945551&vid=7668445095837846799"
    )
    assert canonicalize_share_url(url) == (
        "https://www.douyin.com/video/7669738019598945551"
    )
    # 纯个人主页（无 modal_id）不得改写
    assert canonicalize_share_url("https://www.douyin.com/user/MS4wLjABAAAAUMAd") == (
        "https://www.douyin.com/user/MS4wLjABAAAAUMAd"
    )
    # 视频页原样保留
    assert canonicalize_share_url("https://www.douyin.com/video/123456789") == (
        "https://www.douyin.com/video/123456789"
    )


def test_mobile_toutiao_host_is_short_link_but_ixigua_dx_is_not() -> None:
    assert is_short_link("https://m.toutiao.com/is/Y4Mz_rFLT0s/")
    # m.ixigua.com/dx/ 是可渲染的内容页（PC 站已关停），不是纯跳转短链。
    assert not is_short_link("https://m.ixigua.com/dx/7667766897567536753")


class _FakeResponse:
    def __init__(self, status: int = 200, url: str = "") -> None:
        self.status = status
        self.url = url

    async def dispose(self) -> None:
        return None


class _FakePage:
    def __init__(self, response: _FakeResponse) -> None:
        class _Request:
            async def get(self, _url: str, **_kwargs: Any) -> _FakeResponse:
                return response

        class _Context:
            request = _Request()

        self.context = _Context()


def test_ixigua_dx_link_is_navigated_directly_without_resolution() -> None:
    original = "https://m.ixigua.com/dx/7667766897567536753"
    page = _FakePage(_FakeResponse(url=original))
    # 非短链：不做预解析，原样导航（PC 规范化地址已确认跳下载页）。
    resolved = asyncio.run(resolve_share_link(page, original))
    assert resolved is None


def test_toutiao_is_link_uses_redirect_target() -> None:
    page = _FakePage(
        _FakeResponse(url="https://www.toutiao.com/article/7557112345678901234/?source=m_redirect")
    )
    resolved = asyncio.run(resolve_share_link(page, "https://m.toutiao.com/is/Y4Mz_rFLT0s/"))
    assert resolved == "https://www.toutiao.com/article/7557112345678901234/?source=m_redirect"
