"""Bilibili dedicated extractor offline regressions (FakePage, no network)."""

from __future__ import annotations

import pytest

from src.crawler.content_parser import ContentParser
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms.bilibili import _url_video_ids

_TARGET = {
    "bvid": "BV1G8Gc6REfA",
    "aid": 123_456_789,
    "title": "目标视频标题",
    "desc": "目标视频简介第一行\n简介第二行",
    "pubdate": 1_785_800_000,
    "owner": {"mid": 370_693_787, "name": "目标UP主", "face": "https://cdn.example.test/face.jpg"},
}
_UNRELATED = {
    "bvid": "BV9zz9zz9zz9",
    "aid": 999_000_111,
    "title": "推荐位视频标题不应进入结果",
    "desc": "推荐位简介",
    "pubdate": 1_785_000_000,
    "owner": {"mid": 1, "name": "推荐位UP主"},
}


class _BilibiliPage:
    url = "https://www.bilibili.com/video/BV1G8Gc6REfA/?spm_id_from=333.1007"

    async def evaluate(self, script: str, *_args: object):
        if "platformSelectors" in script:
            return {
                "url": self.url,
                "title": "目标视频标题_哔哩哔哩_bilibili",
                "visibleText": "目标视频标题",
                "canonicalUrl": self.url,
                "meta": {},
                "jsonLd": [],
                "embeddedPayloads": [],
                "domValues": {},
                "platformValues": {},
                "images": [],
            }
        if "__INITIAL_STATE__" in script:
            return {"videoData": _TARGET, "related": [_UNRELATED]}
        return None


def test_url_video_ids_extracts_bvid_and_aid() -> None:
    assert _url_video_ids("https://www.bilibili.com/video/BV1G8Gc6REfA/?x=1") == (
        "BV1G8Gc6REfA",
        None,
    )
    assert _url_video_ids("https://www.bilibili.com/video/av123456789") == (
        None,
        "123456789",
    )
    assert _url_video_ids("https://www.bilibili.com/") == (None, None)


@pytest.mark.asyncio
async def test_bilibili_parser_locks_onto_requested_video() -> None:
    definition = find_platform(_BilibiliPage.url)
    assert definition is not None and definition.key == "bilibili"
    data = await ContentParser().extract(_BilibiliPage(), definition)

    assert data.title == "目标视频标题"
    assert data.content_text == "目标视频简介第一行\n简介第二行"
    assert data.author_name == "目标UP主"
    assert data.author_id == "370693787"
    assert data.author_url == "https://space.bilibili.com/370693787"
    assert data.published_at is not None


class _BilibiliForeignOnlyPage(_BilibiliPage):
    """Hydration payload carries only a foreign (recommendation) node."""

    async def evaluate(self, script: str, *_args: object):
        if "platformSelectors" in script:
            return {
                "url": self.url,
                "title": "页面标题_哔哩哔哩_bilibili",
                "visibleText": "页面标题",
                "canonicalUrl": self.url,
                "meta": {},
                "jsonLd": [],
                "embeddedPayloads": [],
                "domValues": {},
                "platformValues": {"title": "目录标题"},
                "images": [],
            }
        if "__INITIAL_STATE__" in script:
            return {"related": [_UNRELATED]}
        return None


@pytest.mark.asyncio
async def test_bilibili_parser_never_pins_recommendation_fields() -> None:
    definition = find_platform(_BilibiliForeignOnlyPage.url)
    assert definition is not None
    data = await ContentParser().extract(_BilibiliForeignOnlyPage(), definition)
    # 推荐位字段绝不能顶替；标题只能来自目录/通用 DOM 路径。
    assert data.title != "推荐位视频标题不应进入结果"
    assert data.author_name != "推荐位UP主"
