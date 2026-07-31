"""Kuaishou parser integration regressions."""

from __future__ import annotations

import pytest

from src.crawler.content_parser import ContentParser
from src.crawler.platform_catalog import find_platform


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
