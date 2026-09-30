"""LLM 提取解析与反幻觉校验测试（全离线）。"""

from datetime import datetime

from src.domain.models import PageData
from src.llm.extract import (
    apply_extraction,
    needs_fallback,
    parse_extraction_response,
    source_text,
)

_SOURCE = (
    "《关于开展舆情核查的通知》\n"
    "发布时间：2026年5月1日 10:00\n"
    "作者：应急管理部研究中心\n"
    "正文内容……"
)


def test_needs_fallback_gating() -> None:
    complete = PageData(title="t", author_name="a", published_at=datetime(2026, 1, 1))
    assert needs_fallback(complete) is False
    assert needs_fallback(PageData(title="t")) is True
    assert needs_fallback(PageData(author_name="a")) is True


def test_source_text_merges_content_and_ocr() -> None:
    page = PageData(content_text="正文", ocr_text="OCR 文字")
    assert source_text(page, 100) == "正文\nOCR 文字"
    assert source_text(page, 4) == "正文\nO"
    assert source_text(PageData(), 100) == ""


def test_parse_response_strips_fence_and_prose() -> None:
    raw = '好的，结果如下：```json\n{"title": "标题", "author_name": null}\n```'
    assert parse_extraction_response(raw) == {"title": "标题", "author_name": None}
    assert parse_extraction_response("没有 JSON") == {}
    assert parse_extraction_response('{"broken": ') == {}


def test_apply_fills_missing_fields_with_grounding() -> None:
    page = PageData(content_text=_SOURCE)
    data = {
        "title": "关于开展舆情核查的通知",
        "author_name": "应急管理部研究中心",
        "published_at": "2026-05-01 10:00",
    }
    filled = apply_extraction(page, data, _SOURCE)

    assert set(filled) == {"title", "author_name", "published_at"}
    assert page.title == "关于开展舆情核查的通知"
    assert page.author_name == "应急管理部研究中心"
    assert page.published_at is not None
    assert page.published_at.replace(tzinfo=None) == datetime(2026, 5, 1, 10, 0)
    assert page.published_at_raw == "2026-05-01 10:00"


def test_apply_rejects_values_not_in_source() -> None:
    page = PageData(content_text=_SOURCE)
    data = {
        "title": "凭空编造的标题",
        "author_name": "虚构作者甲",
        "published_at": "2025-01-01 08:00",
    }
    assert apply_extraction(page, data, _SOURCE) == []
    assert page.title is None
    assert page.author_name is None
    assert page.published_at is None


def test_apply_does_not_overwrite_existing_fields() -> None:
    page = PageData(title="已有标题", author_name="已有作者", content_text=_SOURCE)
    data = {"title": "关于开展舆情核查的通知", "author_name": "应急管理部研究中心"}
    assert apply_extraction(page, data, _SOURCE) == []
    assert page.title == "已有标题"
    assert page.author_name == "已有作者"


def test_apply_rejects_oversized_or_multiline_values() -> None:
    page = PageData(content_text=_SOURCE)
    data = {
        "title": "关于开展舆情核查的通知\n换行",
        "author_name": "应急管理部研究中心" * 10,
    }
    assert apply_extraction(page, data, _SOURCE) == []
    assert page.title is None
    assert page.author_name is None
