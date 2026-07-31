from __future__ import annotations

from typing import Any

import pytest

from src.crawler.content_parser import ContentParser
from src.crawler.platform_catalog import find_platform

NETEASE_URL = "https://www.163.com/news/article/L35RNISH000189FH.html"
SOHU_URL = (
    "https://tv.sohu.com/v/"
    "dXMvMzg2MjM0NDEzLzczMzkyODA4Mi5zaHRtbA==.html"
)


def _document(
    url: str,
    *,
    platform_values: dict[str, str],
    meta: dict[str, str] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "url": url,
        "title": "站点标题",
        "visibleText": "页面壳和推荐内容",
        "canonicalUrl": url,
        "meta": meta or {},
        "jsonLd": [],
        "embeddedPayloads": [],
        "domValues": {},
        "platformValues": platform_values,
        "images": images or [],
    }


@pytest.mark.asyncio
async def test_parser_keeps_netease_article_above_page_shell_and_network() -> None:
    class Page:
        url = NETEASE_URL

        async def evaluate(self, _script: str, *args: object) -> Any:
            if args:
                return _document(
                    self.url,
                    platform_values={
                        "title": "正确标题",
                        "content_text": "分享至\n正确正文\n网易跟贴",
                        "author_name": "网易",
                        "published_at": "广告倒计时",
                    },
                    meta={"author": "网易"},
                    images=[
                        {
                            "url": "https://cdn.example.test/recommendation.jpg",
                            "width": 800,
                            "height": 600,
                            "alt": "",
                            "context": "recommend-card",
                            "inContent": True,
                        }
                    ],
                )
            return {
                "canonical": self.url,
                "title": "正确标题",
                "content": "第一段正文。\n第二段正文。",
                "author": "新华社客户端",
                "authorUrl": "https://h.xinhuaxmt.com/vh512/share/13222030",
                "published": "2026-07-31 12:36:27",
                "imageUrls": ["https://cdn.example.test/article.jpg"],
            }

    definition = find_platform(NETEASE_URL)
    assert definition is not None
    data = await ContentParser().extract(
        Page(),
        definition,
        network_payloads=(
            {
                "title": "推荐文章标题",
                "content": "推荐文章正文",
                "author": {"name": "推荐作者"},
            },
        ),
    )

    assert data.title == "正确标题"
    assert data.content_text == "第一段正文。\n第二段正文。"
    assert data.author_name == "新华社客户端"
    assert data.published_at is not None
    assert data.image_urls == ["https://cdn.example.test/article.jpg"]


@pytest.mark.asyncio
async def test_parser_keeps_sohu_id_matched_video_above_player_shell() -> None:
    class Page:
        url = SOHU_URL

        async def evaluate(self, _script: str, *args: object) -> Any:
            if args:
                return _document(
                    self.url,
                    platform_values={
                        "title": "搜狐视频",
                        "author_name": "导航账号",
                        "author_url": "https://tv.sohu.com/user/342806571",
                        "published_at": "会员跳广告 9 秒后跳过",
                    },
                    images=[
                        {
                            "url": "https://cdn.example.test/related.jpg",
                            "width": 640,
                            "height": 360,
                            "alt": "",
                            "context": "related-card",
                            "inContent": True,
                        }
                    ],
                )
            return {
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

    definition = find_platform(SOHU_URL)
    assert definition is not None
    data = await ContentParser().extract(
        Page(),
        definition,
        network_payloads=(
            {
                "title": "播放器推荐视频",
                "description": "推荐视频正文",
                "author": {"name": "推荐作者"},
            },
        ),
    )

    assert data.title == "首次公开！机器狼扛着单兵火箭筒冲上滩头"
    assert data.content_text == "首次公开！机器狼扛着单兵火箭筒冲上滩头"
    assert data.author_name == "环视频"
    assert data.author_id == "386234413"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-07-31 08:45:00"
    )
    assert data.image_urls == []
