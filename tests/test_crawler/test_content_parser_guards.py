"""Tests for the unmatched-dedicated guards (douyin / bilibili / tieba).

When the dedicated extractor cannot match the URL's content id, fields read
from network/embedded payloads belong to configuration or recommendation
nodes — never to the requested content.  The guard strips them so a record
goes blank for manual entry instead of exporting wrong evidence.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.crawler.content_parser import ContentParser
from src.crawler.platform_catalog import find_platform
from src.crawler.platforms.tieba import sanitize_tieba_content
from src.domain.models import ExtractionSource, PageData


def _document(**overrides) -> SimpleNamespace:
    base = {"dom_values": {}, "visible_text": "", "title": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


def _payload_sourced(page: PageData, *fields: str) -> PageData:
    for field in fields:
        page.field_sources[field] = ExtractionSource.NETWORK_JSON
    return page


def test_unmatched_guard_strips_payload_fields_and_implausible_time() -> None:
    parser = ContentParser()
    merged = _payload_sourced(
        PageData(
            title="厂牌排名规则",
            content_text="厂牌榜单值由旗下团员当日榜单值综合计算得出。",
            author_name="[马市长]",
            published_at_raw="245000",
            image_urls=["https://cdn.example.test/recommend.jpg"],
        ),
        "title",
        "content_text",
        "author_name",
        "published_at_raw",
    )

    parser._strip_untrusted_payload_fields(
        merged, _document(), "7669738019598945551", strip_images=True
    )

    assert merged.title is None
    assert merged.content_text is None
    assert merged.author_name is None
    assert merged.published_at_raw is None
    assert merged.published_at is None
    assert merged.image_urls == []


def test_unmatched_guard_refills_from_document_dom() -> None:
    parser = ContentParser()
    merged = _payload_sourced(
        PageData(title="厂牌排名规则", content_text="厂牌榜单值…"),
        "title",
        "content_text",
    )
    document = _document(title="抖音 - 视频", visible_text="真实可见的正文内容")

    parser._strip_untrusted_payload_fields(merged, document, "7669738019598945551")

    assert merged.title == "抖音 - 视频"
    assert merged.content_text == "真实可见的正文内容"
    assert merged.field_sources["content_text"] is ExtractionSource.VISIBLE_TEXT


def test_unmatched_guard_keeps_dom_sourced_fields() -> None:
    parser = ContentParser()
    merged = PageData(
        title="真实可见标题",
        content_text="真实可见正文",
        published_at_raw="2026-07-29 17:56",
    )
    merged.field_sources["title"] = ExtractionSource.GENERIC_DOM
    merged.field_sources["content_text"] = ExtractionSource.VISIBLE_TEXT
    merged.field_sources["published_at_raw"] = ExtractionSource.PLATFORM_DOM

    parser._strip_untrusted_payload_fields(merged, _document(), "7669738019598945551")

    assert merged.title == "真实可见标题"
    assert merged.content_text == "真实可见正文"
    assert merged.published_at_raw == "2026-07-29 17:56"


def test_unmatched_guard_inactive_when_url_carries_no_id() -> None:
    parser = ContentParser()
    merged = _payload_sourced(
        PageData(title="载荷标题", content_text="载荷正文"),
        "title",
        "content_text",
    )

    parser._strip_untrusted_payload_fields(merged, _document(), None)

    assert merged.title == "载荷标题"
    assert merged.content_text == "载荷正文"


@pytest.mark.asyncio
async def test_douyin_modal_url_without_aweme_match_never_exports_config_node() -> None:
    """Regression for job 20260805-084301 record 11 (厂牌排名规则 + 245000)."""

    modal_id = "7669738019598945551"
    unrelated_config = {
        "title": "厂牌排名规则",
        "desc": "厂牌榜单值由旗下团员当日榜单值与榜单排名结果综合计算得出。",
        "author": {"nickname": "[马市长]"},
    }

    class DouyinModalPage:
        url = f"https://www.douyin.com/user/SEC-AUTHOR?modal_id={modal_id}"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "抖音 - 用户",
                    "visibleText": "真实可见的正文内容",
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [unrelated_config],
                    "domValues": {},
                    "platformValues": {},
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
            return None

    definition = find_platform(DouyinModalPage.url)
    assert definition is not None
    data = await ContentParser().extract(
        DouyinModalPage(),
        definition,
        network_payloads=(
            {"aweme_detail": {"aweme_id": "1000000000000000000", "desc": "别的视频"}},
            unrelated_config,
        ),
    )

    assert "厂牌" not in (data.title or "")
    assert "厂牌" not in (data.content_text or "")
    assert data.content_text == "真实可见的正文内容"
    assert data.author_name != "[马市长]"
    assert data.published_at is None
    assert data.published_at_raw is None
    assert data.image_urls == []


# ── 贴吧正文净化 ──

def test_sanitize_tieba_content_anchors_at_title_and_cuts_chrome_tail() -> None:
    raw = (
        "1\n2\n3\n东郊到家\n吧\n发贴\n登录\n首页\n我的\n大家都在逛的吧\n抗压背锅\n"
        "核心吧友\n07-31\n福建\n"
        "邵阳东郊真贴心！\n"
        "家里老人行动不便，还好邵阳老家也上线了东郊，不用老人出门居家就能推拿。\n"
        "回复\n点赞\n收藏\n全部回复\n只看楼主\n"
        "关注7226贴子2.5W\n建吧日期：2021.11.07\n百度版权声明：©2026 Baidu\n举报电话：400-921-5119"
    )

    cleaned = sanitize_tieba_content(raw, "邵阳东郊真贴心！-百度贴吧")

    assert cleaned == "家里老人行动不便，还好邵阳老家也上线了东郊，不用老人出门居家就能推拿。"


def test_sanitize_tieba_content_keeps_real_fragment_without_title_anchor() -> None:
    raw = (
        "矛盾日益突出，将会给国家给人民带来深重灾难。\n"
        "回复\n收藏\n全部回复\n只看楼主\n别让楼主寂寞太久哦 ~\n"
        "百度版权声明：©2026 Baidu\n使用百度前必读"
    )

    cleaned = sanitize_tieba_content(raw, "邵阳市中级人民法院专员-百度贴吧")

    assert cleaned == "矛盾日益突出，将会给国家给人民带来深重灾难。"


def test_sanitize_tieba_content_returns_none_when_only_chrome() -> None:
    assert sanitize_tieba_content("登录\n首页\n我的\n大家都在逛的吧", "标题") is None
    assert sanitize_tieba_content("", "标题") is None
    assert sanitize_tieba_content(None, "标题") is None


def test_tieba_pick_time_skips_bar_creation_date() -> None:
    from src.crawler.platforms.tieba import _pick_time

    tail = ["建吧日期：2014.02.18", "吧主很懒，没有留下任何简介", "2026-07-31 12:30"]
    assert _pick_time(tail) == "2026-07-31 12:30"
    assert _pick_time(["建吧日期：2014.02.18"]) is None


@pytest.mark.asyncio
async def test_tieba_finalize_sanitizes_visible_text_fallback() -> None:
    """探针只拿到标题时，通用整页文本兜底必须净化后才能进正文。"""

    garbage = (
        "1\n2\n3\n发贴\n登录\n首页\n我的\n大家都在逛的吧\n抗压背锅\n孙笑川\n"
        "核心吧友\n07-31\n福建\n"
        "邵阳东郊真贴心！\n"
        "家里老人行动不便，还好邵阳老家也上线了东郊。\n"
        "回复\n收藏\n全部回复\n关注7226贴子2.5W\n百度版权声明：©2026 Baidu"
    )

    class TiebaPage:
        url = "https://tieba.baidu.com/p/10908851860"

        async def evaluate(self, script: str, *_args: object):
            if "platformSelectors" in script:
                return {
                    "url": self.url,
                    "title": "邵阳东郊真贴心！-百度贴吧",
                    "visibleText": garbage,
                    "canonicalUrl": self.url,
                    "meta": {},
                    "jsonLd": [],
                    "embeddedPayloads": [],
                    "domValues": {},
                    "platformValues": {},
                    "images": [],
                }
            if "tailInfos" in script:  # tieba DOM 探针：只拿到标题
                return {
                    "title": "邵阳东郊真贴心！-百度贴吧",
                    "content": "",
                    "author": "核心吧友",
                    "authorUrl": "",
                    "tailInfos": [],
                }
            return None

    definition = find_platform(TiebaPage.url)
    assert definition is not None
    data = await ContentParser().extract(TiebaPage(), definition)

    assert data.content_text == "家里老人行动不便，还好邵阳老家也上线了东郊。"
    assert "大家都在逛的吧" not in (data.content_text or "")
    assert "百度版权声明" not in (data.content_text or "")
