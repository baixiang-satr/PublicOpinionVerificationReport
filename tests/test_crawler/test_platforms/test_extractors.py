"""Offline fixture tests for dedicated platform extractors.

No real network: a FakePage returns canned JSON/DOM probe values keyed by a
substring of the evaluated script, and RenderedDocument payloads are built
from synthetic platform-shaped JSON.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.baijiahao import BaijiahaoExtractor
from src.crawler.platforms.bytedance_ssr import ToutiaoExtractor, XiguaExtractor
from src.crawler.platforms.douyin import DouyinExtractor
from src.crawler.platforms.kuaishou import KuaishouExtractor
from src.crawler.platforms.sohu_video import SohuVideoExtractor
from src.crawler.platforms.tieba import TiebaExtractor
from src.crawler.platforms.weibo import WeiboExtractor, _parse_weibo_time
from src.crawler.platforms.xiaohongshu import XiaohongshuExtractor
from src.crawler.platforms.zhihu import ZhihuExtractor
from src.domain.models import ExtractionSource


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


# ── douyin ──
def test_douyin_extracts_aweme_detail_from_render_data() -> None:
    aweme = {
        "desc": "这条视频的文案",
        "createTime": 1_751_000_000_000,
        "author": {"nickname": "测试作者", "unique_id": "tester001", "sec_uid": "SEC123"},
    }
    # The extractor evaluates the page script which already ran
    # decodeURIComponent in the browser, so the fake returns decoded JSON.
    page = FakePage({"RENDER_DATA": json.dumps({"36": {"aweme": {"detail": aweme}}})})
    document = RenderedDocument(url="https://www.douyin.com/video/123")

    data = _run(DouyinExtractor().extract(page, document, _definition("douyin")))

    assert data is not None
    assert data.content_text == "这条视频的文案"
    assert data.author_name == "测试作者"
    assert data.author_id == "tester001"
    assert data.author_url == "https://www.douyin.com/user/SEC123"
    assert data.published_at is not None and data.published_at.year == 2025
    assert data.field_sources["content_text"].value == "embedded_json"


def test_douyin_returns_none_without_aweme() -> None:
    page = FakePage({"RENDER_DATA": json.dumps({"other": {}})})
    document = RenderedDocument(url="https://www.douyin.com/video/123")
    assert _run(DouyinExtractor().extract(page, document, _definition("douyin"))) is None


def test_douyin_prefers_node_matching_requested_aweme_id() -> None:
    """页面同时加载推荐视频详情时，只能提取与请求 aweme_id 匹配的节点。"""

    target = {
        "aweme_id": "7667987870472788815",
        "desc": "目标视频文案",
        "createTime": 1_751_000_000_000,
        "author": {"nickname": "目标作者", "unique_id": "target001", "sec_uid": "SECT"},
    }
    recommendation = {
        "aweme_id": "7667886625339225445",
        "desc": "推荐视频文案",
        "createTime": 1_751_000_000_000,
        "author": {"nickname": "推荐作者", "unique_id": "reco001", "sec_uid": "SECR"},
    }
    # 推荐节点在前：模拟推荐详情先于目标详情到达
    page = FakePage({})
    document = RenderedDocument(
        url="https://www.douyin.com/video/7667987870472788815",
        network_payloads=({"item_list": [recommendation]}, {"aweme_detail": target}),
    )

    data = _run(DouyinExtractor().extract(page, document, _definition("douyin")))

    assert data is not None
    assert data.content_text == "目标视频文案"
    assert data.author_name == "目标作者"
    assert data.author_id == "target001"
    assert data.author_url == "https://www.douyin.com/user/SECT"


def test_douyin_reads_current_aweme_detail_shape_and_visible_minute_time() -> None:
    """Current web API uses top-level aweme_detail + snake_case fields."""

    target = {
        "aweme_id": "7667886625339225445",
        "desc": "道路千万条，安全第一条。",
        "create_time": 1_785_318_978,
        "author": {
            "nickname": "建柱种苗-李文亮",
            "uid": "1234567890123456789",
            "sec_uid": "SECCURRENT",
        },
    }
    page = FakePage({})
    document = RenderedDocument(
        url="https://www.douyin.com/video/7667886625339225445",
        network_payloads=({"aweme_detail": target},),
        visible_text="道路千万条，安全第一条。\n发布时间：2026-07-29 17:56",
    )

    data = _run(DouyinExtractor().extract(page, document, _definition("douyin")))

    assert data is not None
    assert data.title == "道路千万条，安全第一条。"
    assert data.content_text == "道路千万条，安全第一条。"
    assert data.author_name == "建柱种苗-李文亮"
    assert data.author_url == "https://www.douyin.com/user/SECCURRENT"
    assert data.published_at_raw == "2026-07-29 17:56"
    assert data.published_at is not None
    assert data.published_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-29 17:56:00"
    assert data.field_sources["content_text"] == ExtractionSource.NETWORK_JSON
    assert data.field_sources["published_at"] == ExtractionSource.PLATFORM_DOM


def test_douyin_returns_none_when_only_recommendations_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """页面只有推荐节点（目标视频被平台替换）时不得错配数据。"""

    monkeypatch.setenv("POR_DISABLE_API_ASSIST", "1")
    recommendation = {
        "aweme_id": "7667886625339225445",
        "desc": "推荐视频文案",
        "createTime": 1_751_000_000_000,
        "author": {"nickname": "推荐作者", "unique_id": "reco001"},
    }
    page = FakePage({})
    document = RenderedDocument(
        url="https://www.douyin.com/video/7667987870472788815",
        network_payloads=({"item_list": [recommendation]},),
    )

    assert _run(DouyinExtractor().extract(page, document, _definition("douyin"))) is None


# ── kuaishou ──
def test_kuaishou_parses_json_body_photo() -> None:
    body = {
        "result": 1,
        "photo": {
            "caption": "快手视频文案",
            "userName": "快手作者",
            "userId": "ks999",
            "timestamp": 1_751_000_000_000,
        },
    }
    page = FakePage({})
    document = RenderedDocument(
        url="https://www.kuaishou.com/short-video/abc",
        embedded_payloads=(body,),
    )

    data = _run(KuaishouExtractor().extract(page, document, _definition("kuaishou")))

    assert data is not None
    assert data.content_text == "快手视频文案"
    assert data.author_name == "快手作者"
    assert data.author_url == "https://www.kuaishou.com/profile/ks999"
    assert data.published_at is not None


# ── xiaohongshu ──
def test_xiaohongshu_reads_note_detail_map() -> None:
    state = {
        "note": {
            "noteDetailMap": {
                "abc123": {
                    "note": {
                        "title": "笔记标题",
                        "desc": "笔记正文内容",
                        "time": 1_751_000_000_000,
                        "user": {"nickname": "红薯", "userId": "u123"},
                    }
                }
            }
        }
    }
    document = RenderedDocument(
        url="https://www.xiaohongshu.com/explore/abc123",
        embedded_payloads=(state,),
    )

    data = _run(XiaohongshuExtractor().extract(FakePage({}), document, _definition("xiaohongshu")))

    assert data is not None
    assert data.title == "笔记标题"
    assert data.content_text == "笔记正文内容"
    assert data.author_name == "红薯"
    assert data.author_url == "https://www.xiaohongshu.com/user/profile/u123"


# ── weibo ──
def test_weibo_reads_mblog_network_payload() -> None:
    mblog = {
        "ok": 1,
        "data": {
            "text_raw": "微博正文",
            "created_at": "Tue Jul 01 12:30:00 +0800 2025",
            "user": {"screen_name": "微博用户", "idstr": "123456"},
        },
    }
    document = RenderedDocument(
        url="https://weibo.com/123456/abc",
        network_payloads=(mblog,),
    )

    data = _run(WeiboExtractor().extract(FakePage({}), document, _definition("weibo")))

    assert data is not None
    assert data.content_text == "微博正文"
    assert data.author_name == "微博用户"
    assert data.author_url == "https://weibo.com/u/123456"
    assert data.published_at is not None and data.published_at.month == 7


def test_weibo_dom_fallback_when_no_json() -> None:
    probe = {
        "content": "DOM 正文",
        "author": "DOM 作者",
        "authorUrl": "https://weibo.com/u/42",
        "time": "2025-07-01 08:00",
    }
    page = FakePage({"detail_wbtext": probe})
    document = RenderedDocument(url="https://weibo.com/1/2")

    data = _run(WeiboExtractor().extract(page, document, _definition("weibo")))

    assert data is not None
    assert data.content_text == "DOM 正文"
    assert data.published_at is not None and data.published_at.day == 1


def test_weibo_time_parser_is_locale_independent() -> None:
    parsed = _parse_weibo_time("Wed Jan 15 09:05:06 +0800 2026")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 1, 15)
    assert (parsed.hour, parsed.minute, parsed.second) == (9, 5, 6)
    assert _parse_weibo_time("不是时间") is None


# ── zhihu ──
def test_zhihu_reads_js_initial_data_answer() -> None:
    initial = {
        "initialState": {
            "entities": {
                "questions": {"1": {"title": "问题标题"}},
                "answers": {
                    "2": {
                        "content": "<p>回答正文</p>",
                        "createdTime": 1_751_000_000,
                        "author": {"name": "知乎用户", "urlToken": "zhihu-er", "id": "uid9"},
                    }
                },
            }
        }
    }
    page = FakePage({"js-initialData": json.dumps(initial)})
    document = RenderedDocument(url="https://www.zhihu.com/question/1/answer/2")

    data = _run(ZhihuExtractor().extract(page, document, _definition("zhihu")))

    assert data is not None
    assert data.title == "问题标题"
    assert data.content_text == "回答正文"
    assert data.author_name == "知乎用户"
    assert data.author_url == "https://www.zhihu.com/people/zhihu-er"
    assert data.published_at is not None


def test_zhihu_zhuanlan_article() -> None:
    initial = {
        "initialState": {
            "entities": {
                "articles": {
                    "7": {
                        "title": "专栏标题",
                        "content": "<p>专栏正文</p>",
                        "created": 1_751_000_000,
                        "author": {"name": "专栏作者", "urlToken": "writer"},
                    }
                }
            }
        }
    }
    page = FakePage({"js-initialData": json.dumps(initial)})
    document = RenderedDocument(url="https://zhuanlan.zhihu.com/p/7")

    data = _run(ZhihuExtractor().extract(page, document, _definition("zhihu")))

    assert data is not None
    assert data.title == "专栏标题"
    assert data.content_text == "专栏正文"


# ── tieba ──
def test_tieba_reads_first_floor() -> None:
    probe = {
        "title": "帖子标题",
        "content": "楼主正文",
        "author": "楼主昵称",
        "authorUrl": "https://tieba.baidu.com/home/main?un=x",
        "tailInfos": ["1楼", "2025-06-30 22:10"],
    }
    page = FakePage({"tail-info": probe})
    document = RenderedDocument(url="https://tieba.baidu.com/p/10843752913")

    data = _run(TiebaExtractor().extract(page, document, _definition("tieba")))

    assert data is not None
    assert data.title == "帖子标题"
    assert data.content_text == "楼主正文"
    assert data.author_name == "楼主昵称"
    assert data.published_at_raw == "2025-06-30 22:10"
    assert data.published_at is not None


# ── baijiahao ──
def test_baijiahao_reads_preloaded_state() -> None:
    state = {
        "article": {
            "detail": {
                "title": "百家号标题",
                "content": "<p>百家号正文</p>",
                "publish_time": "2025-06-29 10:00:00",
                "author": {"name": "百家号作者", "uk": "uk123"},
            }
        }
    }
    page = FakePage({"__PRELOADED_STATE__": json.dumps(state)})
    document = RenderedDocument(url="https://baijiahao.baidu.com/s?id=1")

    data = _run(BaijiahaoExtractor().extract(page, document, _definition("baijiahao")))

    assert data is not None
    assert data.title == "百家号标题"
    assert data.content_text == "百家号正文"
    assert data.author_name == "百家号作者"
    assert data.published_at is not None


# ── toutiao / ixigua ──
def test_toutiao_reads_article_info() -> None:
    payload = {
        "data": {
            "articleInfo": {
                "title": "头条标题",
                "content": "<p>头条正文</p>",
                "publishTime": 1_751_000_000,
                "mediaInfo": {"name": "头条媒体", "id": "m1"},
            }
        }
    }
    document = RenderedDocument(
        url="https://www.toutiao.com/article/123/",
        embedded_payloads=(payload,),
    )

    data = _run(ToutiaoExtractor().extract(FakePage({}), document, _definition("toutiao")))

    assert data is not None
    assert data.title == "头条标题"
    assert data.content_text == "头条正文"
    assert data.author_name == "头条媒体"
    assert data.author_id == "m1"
    assert data.published_at is not None


def test_toutiao_reads_account_from_media_info_variants() -> None:
    payload = {
        "articleInfo": {
            "title": "头条标题",
            "content": "<p>头条正文</p>",
            "publishTime": 1_751_000_000,
            "media_info": {
                "nickname": "头条作者",
                "mediaId": "media-7788",
                "userUrl": "https://www.toutiao.com/c/user/token/public-token/",
            },
        }
    }
    document = RenderedDocument(
        url="https://www.toutiao.com/article/7788/",
        embedded_payloads=(payload,),
    )

    data = _run(
        ToutiaoExtractor().extract(
            FakePage({}),
            document,
            _definition("toutiao"),
        )
    )

    assert data is not None
    assert data.author_name == "头条作者"
    assert data.author_id == "media-7788"
    assert data.author_url == "https://www.toutiao.com/c/user/token/public-token/"


def test_xigua_reads_video_node() -> None:
    payload = {
        "videoDetail": {
            "title": "西瓜视频标题",
            "abstract": "西瓜视频简介",
            "publish_time": "2025-06-28 09:00:00",
            "userInfo": {"name": "西瓜作者", "user_id": "xg1"},
        }
    }
    document = RenderedDocument(
        url="https://www.ixigua.com/123",
        embedded_payloads=(payload,),
    )

    data = _run(XiguaExtractor().extract(FakePage({}), document, _definition("ixigua")))

    assert data is not None
    assert data.title == "西瓜视频标题"
    assert data.content_text == "西瓜视频简介"
    assert data.author_name == "西瓜作者"
    assert data.author_url == "https://www.ixigua.com/home/xg1"


# ── sohu video ──
def test_sohu_video_fills_author() -> None:
    probe = {
        "title": "搜狐视频标题",
        "desc": "视频简介",
        "author": "张朝阳的物理课",
        "authorUrl": "https://tv.sohu.com/user/123",
        "time": "2025-06-27 20:00",
    }
    page = FakePage({"user-name": probe})
    document = RenderedDocument(url="https://tv.sohu.com/v/abc.html")

    data = _run(SohuVideoExtractor().extract(page, document, _definition("sohu_video")))

    assert data is not None
    assert data.author_name == "张朝阳的物理课"
    assert data.title == "搜狐视频标题"


# ── fallback safety ──
@pytest.mark.parametrize(
    "extractor",
    [
        DouyinExtractor(),
        KuaishouExtractor(),
        XiaohongshuExtractor(),
        WeiboExtractor(),
        ZhihuExtractor(),
        TiebaExtractor(),
        BaijiahaoExtractor(),
        ToutiaoExtractor(),
        XiguaExtractor(),
        SohuVideoExtractor(),
    ],
)
def test_extractors_return_none_on_empty_document(extractor: Any) -> None:
    document = RenderedDocument(url="https://example.test/nothing")
    assert _run(extractor.extract(FakePage({}), document, _definition("x"))) is None
