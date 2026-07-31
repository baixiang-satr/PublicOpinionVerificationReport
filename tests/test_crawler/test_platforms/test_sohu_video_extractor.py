from __future__ import annotations

from typing import Any

import pytest

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms.sohu_video import (
    SohuVideoExtractor,
    sohu_video_identity,
)
from src.domain.models import ExtractionSource

URL = (
    "https://tv.sohu.com/v/"
    "dXMvMzg2MjM0NDEzLzczMzkyODA4Mi5zaHRtbA==.html"
)


class FakePage:
    def __init__(self, response: Any) -> None:
        self.response = response

    async def evaluate(self, _script: str) -> Any:
        return self.response


@pytest.mark.asyncio
async def test_sohu_video_extracts_id_matched_player_globals() -> None:
    probe = {
        "embedded": True,
        "vid": "733928082",
        "uid": "386234413",
        "title": "首次公开！机器狼扛着单兵火箭筒冲上滩头",
        "description": (
            "千里眼视频：首次公开！机器狼扛着单兵火箭筒冲上滩头"
        ),
        "author": "环视频",
        "authorUrl": "https://tv.sohu.com/user/386234413",
        "published": "2026-07-31 08:45",
    }
    definition = find_platform(URL)
    assert definition is not None

    data = await SohuVideoExtractor().extract(
        FakePage(probe),
        RenderedDocument(url=URL),
        definition,
    )

    assert data is not None
    assert data.title == "首次公开！机器狼扛着单兵火箭筒冲上滩头"
    assert data.content_text == "首次公开！机器狼扛着单兵火箭筒冲上滩头"
    assert data.author_name == "环视频"
    assert data.author_id == "386234413"
    assert data.author_url == "https://tv.sohu.com/user/386234413"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-31 08:45:00"
    )
    assert data.field_sources["title"] == ExtractionSource.EMBEDDED_JSON


@pytest.mark.asyncio
async def test_sohu_video_rejects_recommendation_payload() -> None:
    definition = find_platform(URL)
    assert definition is not None
    data = await SohuVideoExtractor().extract(
        FakePage(
            {
                "embedded": True,
                "vid": "999999999",
                "uid": "386234413",
                "title": "推荐视频",
            }
        ),
        RenderedDocument(url=URL),
        definition,
    )
    assert data is None


def test_sohu_video_identity_decodes_current_and_legacy_routes() -> None:
    assert sohu_video_identity(URL) == ("386234413", "733928082")
    assert sohu_video_identity(
        "https://my.tv.sohu.com/us/386234413/733928082.shtml"
    ) == ("386234413", "733928082")
    assert sohu_video_identity(
        "https://tv.sohu.com/v/"
        "MjAyNjA3MjcvbjYyMDIzMDQwMS5zaHRtbA==.html"
    ) == (None, "620230401")
