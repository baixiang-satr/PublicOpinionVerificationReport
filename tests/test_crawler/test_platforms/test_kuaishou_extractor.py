"""Kuaishou target-photo matching regressions."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms import kuaishou
from src.crawler.platforms.kuaishou import KuaishouExtractor


@pytest.fixture(autouse=True)
def _fast_hydration_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询节奏与用例无关；测试里把延迟压到 0 保持快速。"""

    monkeypatch.setattr(kuaishou, "_HYDRATION_POLL_DELAY_MS", 0)


class FakePage:
    async def evaluate(self, _script: str, *_args: object) -> dict[str, Any]:
        return {}


def _extract(document: RenderedDocument):
    definition = find_platform(document.url)
    assert definition is not None
    return asyncio.run(
        KuaishouExtractor().extract(FakePage(), document, definition)
    )


def test_selects_requested_photo_and_exports_public_account() -> None:
    recommendation = {
        "caption": "推荐视频文案",
        "userName": "推荐作者",
        "userId": 999000111,
        "userEid": "3xrecommendation",
        "kwaiId": "recommend_account",
        "photoId": "9000111222333444555",
        "timestamp": 1_785_200_000_000,
    }
    requested = {
        "caption": "当他说出名字的那一刻 就知道他喜欢你了很久很久",
        "userName": "疑xin",
        "userId": 2_375_003_829,
        "userEid": "3xyfg2j5us8wsyg",
        "kwaiId": "vxiaoyi22609",
        "photoId": "5226990523508912741",
        "timestamp": 1_785_251_975_086,
        "coverUrls": [
            {
                "url": (
                    "https://p2.a.yximgs.com/cover.jpg"
                    "?clientCacheKey=3xev27cpa7jba4i.jpg"
                )
            }
        ],
    }
    document = RenderedDocument(
        url=(
            "https://m.gifshow.com/fw/photo/3xev27cpa7jba4i"
            "?photoId=3xev27cpa7jba4i"
            "&shareObjectId=5226990523508912741"
        ),
        embedded_payloads=(
            {"data": {"feeds": [recommendation], "photo": requested}},
        ),
    )

    data = _extract(document)

    assert data is not None
    assert data.title == requested["caption"]
    assert data.content_text == requested["caption"]
    assert data.author_name == "疑xin"
    assert data.author_id == "vxiaoyi22609"
    assert data.author_url == "https://www.kuaishou.com/profile/3xyfg2j5us8wsyg"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-28 23:19:35"
    )


def test_rejects_recommendations_when_requested_photo_is_missing() -> None:
    document = RenderedDocument(
        url=(
            "https://www.kuaishou.com/short-video/3xev27cpa7jba4i"
            "?shareObjectId=5226990523508912741"
        ),
        network_payloads=(
            {
                "feeds": [
                    {
                        "caption": "不属于目标链接的推荐文案",
                        "userName": "推荐作者",
                        "photoId": "9000111222333444555",
                        "timestamp": 1_785_200_000_000,
                    }
                ]
            },
        ),
    )

    assert _extract(document) is None


def test_waits_for_late_init_state_hydration() -> None:
    """INIT_STATE 水合晚于导航稳定判定时，轮询后仍能命中目标 photo。"""

    target = {
        "caption": "晚水合的目标文案",
        "userName": "迟到作者",
        "userEid": "3xlatehyd",
        "photoId": "5226990523508912741",
        "timestamp": 1_785_251_975_086,
    }

    class LatePage:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, _script: str, *_args: object) -> Any:
            self.calls += 1
            if self.calls < 3:
                return None  # 前两次尚未水合
            return {"data": {"photo": target}}

    document = RenderedDocument(
        url=(
            "https://www.kuaishou.com/short-video/3xlatehyd"
            "?photoId=5226990523508912741"
        ),
    )
    definition = find_platform(document.url)
    assert definition is not None
    page = LatePage()

    data = asyncio.run(KuaishouExtractor().extract(page, document, definition))

    assert data is not None
    assert data.content_text == "晚水合的目标文案"
    assert data.author_name == "迟到作者"
    assert page.calls == 3


def test_gives_up_after_bounded_hydration_polls() -> None:
    """一直未水合时有界放弃（不错收推荐，也不无限等待）。"""

    class DryPage:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, _script: str, *_args: object) -> Any:
            self.calls += 1
            return None

    document = RenderedDocument(
        url="https://www.kuaishou.com/short-video/3xev27cpa7jba4i",
    )
    definition = find_platform(document.url)
    assert definition is not None
    page = DryPage()

    result = asyncio.run(KuaishouExtractor().extract(page, document, definition))

    assert result is None
    assert page.calls == 1 + kuaishou._HYDRATION_POLL_ATTEMPTS
