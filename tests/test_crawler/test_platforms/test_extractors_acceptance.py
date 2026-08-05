"""Acceptance-focused extractor regressions (23 条分享链接专项).

Split from ``test_extractors.py`` to keep every file under the 500-line
release-check limit.  Covers the URL forms present in
``tests/test_input/social_share_links.csv``: douyin profile-modal share
links, ixigua group-id locking and toutiao 微头条 (/w/) nodes.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.bytedance_ssr import ToutiaoExtractor, XiguaExtractor
from src.crawler.platforms.douyin import DouyinExtractor


class FakePage:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def evaluate(self, script: str) -> Any:
        for key, value in self._responses.items():
            if key in script:
                return value
        return None


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


def test_douyin_user_modal_url_locks_onto_modal_aweme_id() -> None:
    """/user/{sec_uid}?modal_id={id} 分享形态以 modal_id 作为 aweme 锁定依据。"""

    modal = {
        "aweme_id": "7668445095837846799",
        "desc": "个人页弹窗目标视频文案",
        "createTime": 1_785_800_000_000,
        "author": {"nickname": "目标作者", "unique_id": "modal001", "sec_uid": "SECM"},
    }
    other_video = {
        "aweme_id": "7669000000000000000",
        "desc": "作者其他视频文案不应进入结果",
        "author": {"nickname": "目标作者", "unique_id": "modal001", "sec_uid": "SECM"},
    }
    page = FakePage({})
    document = RenderedDocument(
        url=(
            "https://www.douyin.com/user/MS4wLjABAAAAUMAd-PhEWYOgVbIgyw"
            "?from_tab_name=main&modal_id=7668445095837846799&vid=7668445095837846799"
        ),
        network_payloads=({"aweme_detail": other_video}, {"aweme_detail": modal}),
    )

    data = _run(DouyinExtractor().extract(page, document, _definition("douyin")))

    assert data is not None
    assert data.content_text == "个人页弹窗目标视频文案"
    assert data.author_name == "目标作者"
    assert data.author_id == "modal001"


def test_xigua_locks_onto_url_group_id_over_recommendations() -> None:
    """URL 带 group id 时，推荐位节点绝不能顶替目标视频。"""

    recommendation = {
        "id": "7000000000000000001",
        "title": "推荐位西瓜标题",
        "abstract": "推荐位简介",
        "userInfo": {"name": "推荐作者", "user_id": "reco1"},
    }
    target = {
        "group_id": "7667766897567536753",
        "title": "目标西瓜标题",
        "abstract": "目标西瓜简介",
        "publish_time": "2026-07-30 10:00:00",
        "userInfo": {"name": "目标作者", "user_id": "xg-target"},
    }
    document = RenderedDocument(
        url="https://www.ixigua.com/video/7667766897567536753",
        embedded_payloads=({"list": [recommendation, target]},),
    )

    data = _run(XiguaExtractor().extract(FakePage({}), document, _definition("ixigua")))

    assert data is not None
    assert data.title == "目标西瓜标题"
    assert data.author_name == "目标作者"
    assert data.author_url == "https://www.ixigua.com/home/xg-target"


def test_xigua_returns_none_when_group_id_absent_from_payload() -> None:
    foreign = {
        "id": "7000000000000000001",
        "title": "推荐位西瓜标题",
        "userInfo": {"name": "推荐作者"},
    }
    document = RenderedDocument(
        url="https://www.ixigua.com/video/7667766897567536753",
        embedded_payloads=({"list": [foreign]},),
    )
    assert _run(
        XiguaExtractor().extract(FakePage({}), document, _definition("ixigua"))
    ) is None


def test_xigua_mobile_share_page_dom_probe() -> None:
    """m.ixigua.com/dx/ 移动页：无语义 hydration 时的 DOM 探测路径。"""

    probe = {
        "title": "农村超肥大猪700多斤，膘子和大豆腐似的，老孟一会就卖半头",
        "author": "立福128",
        "published": "2026-07-29",
    }
    page = FakePage({"xigua-feedtitle": probe})
    document = RenderedDocument(url="https://m.ixigua.com/dx/7667766897567536753")

    data = _run(XiguaExtractor().extract(page, document, _definition("ixigua")))

    assert data is not None
    assert data.title == "农村超肥大猪700多斤，膘子和大豆腐似的，老孟一会就卖半头"
    assert data.content_text == data.title
    assert data.author_name == "立福128"
    assert data.published_at_raw == "2026-07-29"
    assert data.published_at is not None


def test_xigua_mobile_probe_returns_none_on_empty_page() -> None:
    page = FakePage({"xigua-feedtitle": {"title": "", "author": "", "published": ""}})
    document = RenderedDocument(url="https://m.ixigua.com/dx/7667766897567536753")
    assert _run(
        XiguaExtractor().extract(page, document, _definition("ixigua"))
    ) is None


def test_toutiao_reads_weitoutiao_node() -> None:
    payload = {
        "data": {
            "id": "1872455359275008",
            "content": "微头条正文内容",
            "create_time": 1_785_800_000,
            "user": {"screen_name": "微头条作者", "user_id": "tt-user-1"},
        }
    }
    document = RenderedDocument(
        url="https://www.toutiao.com/w/1872455359275008/",
        embedded_payloads=(payload,),
    )

    data = _run(ToutiaoExtractor().extract(FakePage({}), document, _definition("toutiao")))

    assert data is not None
    assert data.content_text == "微头条正文内容"
    assert data.author_name == "微头条作者"
    assert data.author_id == "tt-user-1"
    assert data.published_at is not None
