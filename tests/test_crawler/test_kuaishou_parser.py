"""Kuaishou parser integration regressions."""

from __future__ import annotations

import pytest

from src.crawler.content_parser import ContentParser
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms import kuaishou


@pytest.fixture(autouse=True)
def _fast_hydration_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kuaishou, "_HYDRATION_POLL_DELAY_MS", 0)


@pytest.mark.asyncio
async def test_kuaishou_parser_keeps_requested_photo_authoritative() -> None:
    target = {
        "caption": "目标快手视频文案",
        "timestamp": 1_785_251_975_086,
        "userName": "疑xin",
        "userId": 2_375_003_829,
        "userEid": "3xyfg2j5us8wsyg",
        "kwaiId": "vxiaoyi22609",
        "photoId": "5226990523508912741",
    }
    unrelated = {
        "caption": "推荐视频文案不应进入结果",
        "timestamp": 1_785_200_000_000,
        "userName": "推荐作者",
        "userId": 999000111,
        "userEid": "3xrecommendation",
        "kwaiId": "recommend_account",
        "photoId": "9000111222333444555",
    }

    class KuaishouPage:
        url = (
            "https://m.gifshow.com/fw/photo/3xev27cpa7jba4i"
            "?photoId=3xev27cpa7jba4i"
            "&shareObjectId=5226990523508912741"
        )

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "更多精彩视频等你来看",
                    "visibleText": "目标快手视频文案",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [{"feeds": [unrelated]}],
                    "domValues": {},
                    "platformValues": {},
                    "images": [
                        {
                            "url": "https://cdn.example.test/recommendation.jpg",
                            "width": 720,
                            "height": 1280,
                            "alt": "",
                            "context": "recommend-card",
                            "inContent": True,
                        }
                    ],
                }
            if "window.INIT_STATE" in script:
                return {"data": {"photo": target, "feeds": [unrelated]}}
            return None

    definition = find_platform(KuaishouPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        KuaishouPage(),
        definition,
        network_payloads=({"data": {"feeds": [unrelated]}},),
    )

    assert data.title == "目标快手视频文案"
    assert data.content_text == "目标快手视频文案"
    assert data.author_name == "疑xin"
    assert data.author_id == "vxiaoyi22609"
    assert data.author_url == "https://www.kuaishou.com/profile/3xyfg2j5us8wsyg"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-28 23:19:35"
    )
    assert data.image_urls == []


@pytest.mark.asyncio
async def test_kuaishou_parser_strips_payloads_when_target_photo_missing() -> None:
    """目标 photo 未命中（水合失败）时，载荷里的推荐/配置节点不得成为证据。"""

    config_like = {
        "caption": "配置节点文案不应进入结果",
        "timestamp": 1_785_200_000_000,
        "userName": "配置作者",
        "kwaiId": "config_account",
    }
    recommendation = {
        "caption": "推荐视频文案不应进入结果",
        "timestamp": 1_785_200_000_000,
        "userName": "推荐作者",
        "userEid": "3xrecommendation",
        "photoId": "9000111222333444555",
    }

    class KuaishouShellPage:
        url = "https://www.kuaishou.com/short-video/3xev27cpa7jba4i"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "更多精彩视频等你来看",
                    "visibleText": "快手壳页面可见文本",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [
                        {"config": config_like, "feeds": [recommendation]}
                    ],
                    "domValues": {},
                    "platformValues": {},
                    "images": [],
                }
            return None  # INIT_STATE 始终未水合

    definition = find_platform(KuaishouShellPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        KuaishouShellPage(),
        definition,
        network_payloads=({"data": {"feeds": [recommendation]}},),
    )

    payload_captions = {config_like["caption"], recommendation["caption"]}
    assert data.title not in payload_captions
    assert data.content_text not in payload_captions
    assert data.author_name not in {"配置作者", "推荐作者"}
    assert data.author_id != "config_account"
