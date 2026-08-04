"""Effective per-column display values for the sheet-style tables.

Both the read-only crawl-result tabs and the editable review grid render the
exact fixed-template columns.  This Qt-free helper computes what each column
should show for one record — manual overrides win over crawled values, and
screenshot/attachment columns follow the same naming rules as export — so
"what you see is what gets written" (所见即导出).
"""
from __future__ import annotations

from src.domain.models import RecordResult
from src.domain.template_schema import SheetLayout
from src.services.review_session import ReviewSession


def row_values(
    session: ReviewSession,
    record: RecordResult,
    layout: SheetLayout,
) -> dict[str, str]:
    """column letter -> display text for one record on ``layout``'s sheet."""

    views = {view.field: view for view in session.field_views(record.task.evidence_id)}
    values: dict[str, str] = {}
    for field, column in layout.field_columns.items():
        if field == "url":
            values[column] = record.task.original_url
        elif field in views:
            values[column] = views[field].value
    primary_column = layout.primary_screenshot_column
    if primary_column:
        name = session.primary_screenshot_name(record)
        if name:
            values[primary_column] = name
    attachment_column = layout.attachment_column
    if attachment_column:
        names = attachment_names(session, record)
        if names:
            values[attachment_column] = ",".join(names)
    return values


def attachment_names(session: ReviewSession, record: RecordResult) -> list[str]:
    """Slot-wise merge so the display matches the export row exactly.

    附件列第一槽位与额外附件槽位各自遵循“人工优先于抓取”，与
    ``apply_override`` 重写 :class:`AssetSet` 后再行映射的方式一致。
    常规表第一槽位是个人主页截图；对调表（``homepage_screenshot_primary``）
    第一槽位是内容页截图（个人主页截图已入主截图列）。
    """

    layout = session.layout_for(record)
    swapped = layout is not None and layout.homepage_screenshot_primary
    override = session.get_override(record.task.evidence_id)
    names: list[str] = []
    first_slot = (
        session.content_screenshot_name(record)
        if swapped
        else session.author_screenshot_name(record)
    )
    if first_slot:
        names.append(first_slot)
    if override is not None and override.attachment_names:
        names.extend(override.attachment_names)
    else:
        names.extend(path.name for path in record.assets.extra_attachments)
    return names


def field_for_column(layout: SheetLayout, column: str) -> str | None:
    """Reverse lookup of ``layout.field_columns`` (first match wins)."""

    for field, letter in layout.field_columns.items():
        if letter == column:
            return field
    return None
