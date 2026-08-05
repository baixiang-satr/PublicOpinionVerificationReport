"""Platform-parser integration tests via ContentParser (douyin / xiaohongshu).

Split from ``test_content_parser.py`` to keep every file under the 500-line
release-check limit.
"""
from __future__ import annotations

import json

import pytest

from src.crawler.content_parser import ContentParser
from src.crawler.platform_catalog import find_platform
from src.domain.models import ExtractionSource, PageData


def test_ixigua_finalize_strips_title_suffix_and_boilerplate() -> None:
    parser = ContentParser()
    merged = PageData(
        title="农村超肥大猪700多斤 | 西瓜视频",
        content_text=(
            "农村超肥大猪700多斤,于2026年7月29日上线。"
            "西瓜视频为您提供高清视频，画面清晰、播放流畅。"
        ),
    )
    parser._finalize_ixigua_video(merged)
    assert merged.title == "农村超肥大猪700多斤"
    assert merged.content_text == "农村超肥大猪700多斤"


def test_douyin_finalize_drops_implausible_digit_publish_time() -> None:
    parser = ContentParser()
    dedicated = PageData(title="厂牌排名规则")
    dedicated.field_sources["title"] = ExtractionSource.NETWORK_JSON
    merged = PageData(
        title="厂牌排名规则",
        published_at_raw="245000",
    )
    merged.field_sources["published_at_raw"] = ExtractionSource.NETWORK_JSON
    merged.field_sources["published_at"] = ExtractionSource.NETWORK_JSON
    parser._finalize_douyin_video(merged, dedicated)
    assert merged.published_at_raw is None
    assert merged.published_at is None


@pytest.mark.asyncio
async def test_douyin_parser_keeps_target_aweme_over_unscoped_config_node() -> None:
    """Regression for the two real short links reported on 2026-07-31."""

    target_id = "7667886625339225445"
    target = {
        "aweme_id": target_id,
        "desc": "道路千万条，安全第一条。",
        "create_time": 1_785_318_978,
        "author": {
            "nickname": "建柱种苗-李文亮",
            "uid": "1234567890123456789",
            "sec_uid": "SEC-TARGET",
        },
    }
    unrelated_config = {
        "title": "厂牌排名规则",
        "desc": "厂牌榜单值由旗下团员当日榜单值与榜单排名结果综合计算得出。",
    }

    class DouyinPage:
        url = f"https://www.douyin.com/video/{target_id}"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "抖音 - 视频",
                    "visibleText": "道路千万条，安全第一条。",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [unrelated_config],
                    "domValues": {},
                    "platformValues": {
                        "content_text": "道路千万条，安全第一条。",
                        "author_name": "建柱种苗-李文亮",
                        "published_at": "2026-07-29 17:56",
                    },
                    "images": [
                        {
                            "url": "https://cdn.example.test/recommendation.jpg",
                            "width": 640,
                            "height": 360,
                            "alt": "",
                            "context": "related-card",
                            "inContent": False,
                        }
                    ],
                }
            if "RENDER_DATA" in script:
                return json.dumps({"config": unrelated_config})
            return None

    definition = find_platform(DouyinPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        DouyinPage(),
        definition,
        network_payloads=({"aweme_detail": target},),
    )

    assert data.title == "道路千万条，安全第一条。"
    assert data.content_text == "道路千万条，安全第一条。"
    assert "厂牌" not in (data.content_text or "")
    assert data.author_name == "建柱种苗-李文亮"
    assert data.author_url == "https://www.douyin.com/user/SEC-TARGET"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-29 17:56:00"
    assert data.image_urls == []


@pytest.mark.asyncio
async def test_xiaohongshu_parser_keeps_url_matched_note_authoritative() -> None:
    target_id = "6a5dd45a0000000001033293"
    target = {
        "noteId": target_id,
        "title": "目标笔记标题",
        "desc": "目标正文#kimi[话题]#",
        "time": 1_784_534_106_000,
        "imageList": [
            {"urlDefault": "https://sns-img.example.test/target.webp"},
        ],
        "user": {
            "nickname": "目标作者",
            "userId": "target-user",
        },
    }
    unrelated = {
        "noteId": "6b0000000000000000000000",
        "title": "推荐笔记标题",
        "desc": "推荐卡片的正文不应进入结果",
        "time": 1_784_000_000_000,
        "user": {
            "nickname": "推荐作者",
            "userId": "other-user",
        },
    }

    class XiaohongshuPage:
        url = f"https://www.xiaohongshu.com/explore/{target_id}"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "目标笔记标题 - 小红书",
                    "visibleText": "目标正文",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [],
                    "domValues": {},
                    "platformValues": {},
                    "images": [
                        {
                            "url": "https://sns-img.example.test/recommendation.webp",
                            "width": 1080,
                            "height": 1440,
                            "alt": "",
                            "context": "recommend-card",
                            "inContent": True,
                        }
                    ],
                }
            if "window.__INITIAL_STATE__" in script:
                return target
            return None

    definition = find_platform(XiaohongshuPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        XiaohongshuPage(),
        definition,
        network_payloads=({"data": {"items": [{"note_card": unrelated}]}},),
    )

    assert data.title == "目标笔记标题"
    assert data.content_text == "目标正文#kimi"
    assert data.author_name == "目标作者"
    assert data.author_id == "target-user"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-20 15:55:06"
    )
    assert data.image_urls == [
        "https://sns-img.example.test/target.webp",
    ]
