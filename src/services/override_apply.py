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
    for field, page_field in _PAGE_FIELD_MAP.items():
        value = override.values.get(field)
        if value and value.strip():
            setattr(page, page_field, value.strip())
            page.field_sources[page_field] = ExtractionSource.MANUAL
            page.field_confidences[page_field] = 1.0
    _apply_published_at(record, override)
    _apply_text_type(record, override)
    if override.primary_screenshot_name:
        record.assets.page_screenshot = Path(override.primary_screenshot_name)
    if override.attachment_names:
        record.assets.extra_attachments = [
            Path(name) for name in override.attachment_names
        ]


def _apply_published_at(record: RecordResult, override: ManualOverride) -> None:
    raw = (override.values.get("published_at") or "").strip()
    if not raw:
        return
    parsed = _parse_manual_datetime(raw)
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


def _parse_manual_datetime(raw: str) -> datetime | None:
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
    return None
