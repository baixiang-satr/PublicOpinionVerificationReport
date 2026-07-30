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
    """Manual attachment names win; otherwise the crawled asset names."""

    override = session.get_override(record.task.evidence_id)
    if override is not None and override.attachment_names:
        return list(override.attachment_names)
    return [path.name for path in record.assets.attachment_paths()]


def field_for_column(layout: SheetLayout, column: str) -> str | None:
    """Reverse lookup of ``layout.field_columns`` (first match wins)."""

    for field, letter in layout.field_columns.items():
        if letter == column:
            return field
    return None
