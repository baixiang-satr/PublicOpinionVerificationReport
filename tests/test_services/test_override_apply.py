from datetime import datetime
from pathlib import Path

from src.domain.models import (
    AssetSet,
    ExtractionSource,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.domain.overrides import ManualOverride
from src.services.override_apply import apply_overrides


def _record(evidence_id: int = 1, sheet: str = "微博博客") -> RecordResult:
    task = UrlTask(
        evidence_id,
        f"https://example.test/post/{evidence_id}",
        f"https://example.test/post/{evidence_id}",
    )
    platform_by_sheet = {
        "微博博客": "新浪_新浪微博_博客贴吧",
        "电商平台": "拼多多",
    }
    return RecordResult(
        task,
        RecordStatus.NEEDS_REVIEW,
        page=PageData(title="抓取标题", content_text="抓取正文"),
        route=RouteDecision(sheet, platform_by_sheet[sheet], "正文"),
        assets=AssetSet(),
    )


def test_apply_overrides_manual_fields_win() -> None:
    record = _record()
    override = ManualOverride(
        evidence_id=1,
        values={
            "title": "人工标题",
            "content": "人工正文",
            "author_name": "人工昵称",
            "published_at": "2026-07-15 10:20:30",
        },
    )

    apply_overrides([record], [override])

    assert record.page.title == "人工标题"
    assert record.page.content_text == "人工正文"
    assert record.page.author_name == "人工昵称"
    assert record.page.field_sources["title"] is ExtractionSource.MANUAL
    assert record.page.field_confidences["title"] == 1.0
    assert record.page.published_at == datetime(2026, 7, 15, 10, 20, 30)
    assert record.page.field_sources["published_at"] is ExtractionSource.MANUAL


def test_apply_overrides_keeps_crawled_values_when_not_overridden() -> None:
    record = _record()
    override = ManualOverride(evidence_id=1, values={"author_name": "人工昵称"})

    apply_overrides([record], [override])

    assert record.page.title == "抓取标题"
    assert record.page.content_text == "抓取正文"
    assert record.page.author_name == "人工昵称"


def test_apply_overrides_validates_text_type_against_sheet() -> None:
    record = _record(sheet="电商平台")
    record.route = RouteDecision("电商平台", "拼多多", "商家")
    good = ManualOverride(evidence_id=1, values={"text_type": "评论回复"})

    apply_overrides([record], [good])
    assert record.route is not None
    assert record.route.text_type == "评论回复"

    bad = ManualOverride(evidence_id=1, values={"text_type": "正文"})
    apply_overrides([record], [bad])
    assert record.route is not None
    assert record.route.text_type == "评论回复"  # unchanged
    assert any(error.code == "MANUAL_TEXT_TYPE_INVALID" for error in record.errors)


def test_apply_overrides_empty_value_clears_field() -> None:
    """空串人工值=显式清空：page 字段置空而不是回落爬取值。"""

    record = _record()
    override = ManualOverride(evidence_id=1, values={"title": "", "content": "人工正文"})

    apply_overrides([record], [override])

    assert record.page.title is None
    assert record.page.field_sources["title"] is ExtractionSource.MANUAL
    assert record.page.content_text == "人工正文"


def test_apply_overrides_empty_published_at_clears() -> None:
    record = _record()
    record.page.published_at = datetime(2026, 8, 1, 9, 0, 0)
    override = ManualOverride(evidence_id=1, values={"published_at": ""})

    apply_overrides([record], [override])

    assert record.page.published_at is None
    assert record.page.field_sources["published_at"] is ExtractionSource.MANUAL


def test_apply_overrides_accepts_chinese_date_text() -> None:
    """人工解析器与编辑校验共用，中文日期格式「2026年9月1日」可导出。"""

    record = _record()
    override = ManualOverride(evidence_id=1, values={"published_at": "2026年9月1日"})

    apply_overrides([record], [override])

    parsed = record.page.published_at
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 9, 1)
    assert not any(
        error.code == "MANUAL_PUBLISHED_AT_INVALID" for error in record.errors
    )


def test_apply_overrides_wechat_author_id_maps_to_author_name() -> None:
    """公众号表「微信号(必填)」列人工值交付为公众号昵称（三层对齐契约）。"""

    task = UrlTask(1, "https://example.test/a", "https://example.test/a")
    record = RecordResult(
        task,
        RecordStatus.NEEDS_REVIEW,
        page=PageData(author_name="旧昵称"),
        route=RouteDecision("公众号", "微信-公众号", "正文"),
        assets=AssetSet(),
    )
    override = ManualOverride(evidence_id=1, values={"author_id": "gh_人工微信号"})

    apply_overrides([record], [override])

    assert record.page.author_name == "gh_人工微信号"
    assert record.page.author_id is None
    assert record.page.field_sources["author_name"] is ExtractionSource.MANUAL


def test_apply_overrides_invalid_datetime_is_audited_not_fatal() -> None:
    record = _record()
    override = ManualOverride(evidence_id=1, values={"published_at": "不是日期"})

    apply_overrides([record], [override])

    assert record.page.published_at is None
    assert any(
        error.code == "MANUAL_PUBLISHED_AT_INVALID" for error in record.errors
    )


def test_apply_overrides_sets_screenshot_and_attachments() -> None:
    record = _record()
    override = ManualOverride(
        evidence_id=1,
        values={},
        primary_screenshot_name="001_manual.png",
        author_screenshot_name="001_author.png",
        attachment_names=["001_extra.png"],
    )

    apply_overrides([record], [override])

    assert record.assets.page_screenshot == Path("001_manual.png")
    assert record.assets.author_screenshot == Path("001_author.png")
    assert record.assets.extra_attachments == [Path("001_extra.png")]
    attachment_names = [path.name for path in record.assets.attachment_paths()]
    assert "001_author.png" in attachment_names
    assert "001_extra.png" in attachment_names


def test_apply_overrides_skips_empty_and_foreign_ids() -> None:
    record = _record(evidence_id=2)
    other = ManualOverride(evidence_id=99, values={"title": "别人的"})
    empty = ManualOverride(evidence_id=2)

    apply_overrides([record], [other, empty])

    assert record.page.title == "抓取标题"
