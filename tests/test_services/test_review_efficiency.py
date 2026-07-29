"""Tests for ReviewSession efficiency helpers (batch ops, copy, completion)."""
from pathlib import Path

from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.services.review_session import ReviewSession


def _record(
    evidence_id: int,
    *,
    sheet: str = "微博博客",
    platform: str = "新浪_新浪微博_博客贴吧",
    text_type: str = "正文",
    status: RecordStatus = RecordStatus.NEEDS_REVIEW,
    **page_kwargs,
) -> RecordResult:
    task = UrlTask(
        evidence_id,
        f"https://example.test/p/{evidence_id}",
        f"https://example.test/p/{evidence_id}",
    )
    return RecordResult(
        task,
        status,
        page=PageData(**page_kwargs),
        route=RouteDecision(sheet, platform, text_type),
        assets=AssetSet(),
    )


def test_set_text_type_many_applies_only_where_allowed(tmp_path: Path) -> None:
    weibo = _record(1)
    commerce = _record(
        2,
        sheet="电商平台",
        platform="拼多多",
        text_type="商家",
    )
    session = ReviewSession.from_records(tmp_path, [weibo, commerce])

    # 商家 is only valid on the commerce sheet.
    skipped = session.set_text_type_many([1, 2], "商家")
    assert skipped == [1]
    assert session.get_override(1) is None
    assert session.get_override(2).values["text_type"] == "商家"

    # 评论回复 is valid on both sheets here.
    skipped = session.set_text_type_many([1, 2], "评论回复")
    assert skipped == []
    assert session.get_override(1).values["text_type"] == "评论回复"


def test_copy_empty_fields_never_overwrites(tmp_path: Path) -> None:
    source = _record(
        1,
        author_name="源作者",
        content_text="源正文",
    )
    target = _record(2, author_name="目标已有作者")
    session = ReviewSession.from_records(tmp_path, [source, target])
    session.set_field(1, "published_at", "2026-07-01 10:00:00")

    copied = session.copy_empty_fields_from(1, 2)

    assert "content" in copied
    assert "published_at" in copied
    assert "author_name" not in copied  # target already has one
    override = session.get_override(2)
    assert override.values["content"] == "源正文"
    assert override.values["published_at"] == "2026-07-01 10:00:00"
    assert "author_name" not in override.values


def test_copy_empty_fields_requires_distinct_existing_ids(tmp_path: Path) -> None:
    record = _record(1, author_name="作者")
    session = ReviewSession.from_records(tmp_path, [record])
    assert session.copy_empty_fields_from(1, 1) == []
    assert session.copy_empty_fields_from(1, 99) == []


def test_sheet_completion_groups_by_sheet(tmp_path: Path) -> None:
    complete = _record(
        1,
        status=RecordStatus.ASSETS_READY,
        author_name="a",
        content_text="c",
    )
    incomplete = _record(2)
    session = ReviewSession.from_records(tmp_path, [complete, incomplete])
    session.set_primary_screenshot(1, "001.png")

    completion = session.sheet_completion()

    assert completion["微博博客"] == (1, 2)


def test_previous_id(tmp_path: Path) -> None:
    records = [_record(1), _record(2), _record(3)]
    session = ReviewSession.from_records(tmp_path, records)
    assert session.previous_id(1) is None
    assert session.previous_id(2) == 1
    assert session.previous_id(3) == 2
    assert session.previous_id(99) is None
