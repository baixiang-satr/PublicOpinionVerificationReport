"""Apply persisted manual overrides onto runtime records before export.

Manual values are human truth: they overwrite crawled fields directly, are
tagged with :data:`ExtractionSource.MANUAL` and confidence 1.0, and never go
through heuristic normalisation.  Invalid values (unknown text type for the
sheet, unparsable datetime) are rejected with an auditable ``TaskError``
instead of silently corrupting the fixed template row.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
from typing import Iterable

from src.domain.models import (
    ExtractionSource,
    RecordResult,
    TaskError,
)
from src.domain.overrides import ManualOverride
from src.domain.template_schema import get_sheet_layout
from src.utils.time_utils import parse_published_at_from_text

logger = logging.getLogger(__name__)

_PAGE_FIELD_MAP = {
    "title": "title",
    "content": "content_text",
    "author_name": "author_name",
    "author_id": "author_id",
    "account_uin": "account_uin",
    "store_name": "store_name",
}


def apply_overrides(
    records: Iterable[RecordResult],
    overrides: Iterable[ManualOverride],
) -> None:
    by_id = {
        override.evidence_id: override
        for override in overrides
        if not override.is_empty()
    }
    if not by_id:
        return
    for record in records:
        override = by_id.get(record.task.evidence_id)
        if override is not None:
            apply_override(record, override)


def apply_override(record: RecordResult, override: ManualOverride) -> None:
    page = record.page
    author_id_target = (
        "author_name" if _uses_author_name_as_id(record) else "author_id"
    )
    for field, page_field in _PAGE_FIELD_MAP.items():
        if field not in override.values:
            continue
        value = override.values[field].strip()
        target_field = author_id_target if field == "author_id" else page_field
        # 空串=显式清空（导出空单元格）；非空=人工值直接覆盖爬取值
        setattr(page, target_field, value or None)
        page.field_sources[target_field] = ExtractionSource.MANUAL
        page.field_confidences[target_field] = 1.0
    _apply_published_at(record, override)
    _apply_text_type(record, override)
    _apply_platform(record, override)
    if override.primary_screenshot_name:
        record.assets.page_screenshot = Path(override.primary_screenshot_name)
    if override.author_screenshot_name:
        record.assets.author_screenshot = Path(override.author_screenshot_name)
    if override.attachment_names:
        record.assets.extra_attachments = [
            Path(name) for name in override.attachment_names
        ]


def _uses_author_name_as_id(record: RecordResult) -> bool:
    """公众号表「微信号(必填)」列直接交付公众号昵称：author_id 人工值改写 author_name。

    与 ``review_session._effective_value`` 及 ``row_mapper`` 的三层对齐契约一致，
    否则该列的人工编辑会在导出时被强制回写成旧昵称。
    """

    if record.route is None:
        return False
    layout = get_sheet_layout(record.route.sheet_name)
    return "account_uin" in layout.field_columns


def _apply_published_at(record: RecordResult, override: ManualOverride) -> None:
    if "published_at" not in override.values:
        return
    raw = override.values["published_at"].strip()
    if not raw:
        # 显式清空发布时间：导出为空单元格
        record.page.published_at = None
        record.page.published_at_raw = ""
        record.page.field_sources["published_at"] = ExtractionSource.MANUAL
        record.page.field_confidences["published_at"] = 1.0
        return
    parsed = parse_manual_datetime(raw)
    if parsed is None:
        record.errors.append(
            TaskError(
                "manual_override",
                "MANUAL_PUBLISHED_AT_INVALID",
                f"人工填写的发布时间无法解析：{raw!r}，已保留原值。",
                retryable=False,
            )
        )
        return
    record.page.published_at = parsed
    record.page.published_at_raw = raw
    record.page.field_sources["published_at"] = ExtractionSource.MANUAL
    record.page.field_confidences["published_at"] = 1.0


def _apply_text_type(record: RecordResult, override: ManualOverride) -> None:
    value = (override.values.get("text_type") or "").strip()
    if not value:
        return
    if record.route is None:
        record.errors.append(
            TaskError(
                "manual_override",
                "MANUAL_TEXT_TYPE_WITHOUT_ROUTE",
                "记录尚未路由到工作表，人工设置的文本类型未生效。",
                retryable=False,
            )
        )
        return
    layout = get_sheet_layout(record.route.sheet_name)
    text_type_column = layout.field_columns.get("text_type")
    allowed = layout.validation_values.get(text_type_column or "", ())
    if value not in allowed:
        record.errors.append(
            TaskError(
                "manual_override",
                "MANUAL_TEXT_TYPE_INVALID",
                f"人工设置的文本类型 {value!r} 不在工作表允许值 {allowed} 内。",
                retryable=False,
            )
        )
        return
    record.route = replace(record.route, text_type=value)


def _apply_platform(record: RecordResult, override: ManualOverride) -> None:
    value = (override.values.get("platform") or "").strip()
    if not value or record.route is None:
        return
    layout = get_sheet_layout(record.route.sheet_name)
    platform_column = layout.field_columns.get("platform")
    allowed = layout.validation_values.get(platform_column or "", ())
    if value not in allowed:
        record.errors.append(
            TaskError(
                "manual_override",
                "MANUAL_PLATFORM_INVALID",
                f"人工设置的发布平台 {value!r} 不在工作表允许值 {allowed} 内。",
                retryable=False,
            )
        )
        return
    record.route = replace(record.route, platform_value=value)


def parse_manual_datetime(raw: str) -> datetime | None:
    """解析人工填写的发布时间；编辑校验与导出共用，保证「存得上就导得出」。

    除 ISO/常见格式外兼容中文日期（「2026年9月1日」等，经
    :func:`parse_published_at_from_text`）；纯数字垃圾文本不轻易放行。
    """

    text = raw.strip()
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    if any(marker in text for marker in ("年", "月", "日", "今天", "昨天")):
        return parse_published_at_from_text(text)
    return None
