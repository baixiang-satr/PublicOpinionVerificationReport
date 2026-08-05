"""Restore URL-scoped dedicated-extractor facts onto the merged page data.

Split from ``content_parser`` to keep that module under the line cap.  Once a
dedicated extractor matched the URL's content id, its fields are authoritative
over page-shell fallbacks (recommendation/config nodes are not evidence).
"""

from __future__ import annotations

from src.domain.models import PageData


def restore_dedicated_fields(merged: PageData, dedicated: PageData) -> None:
    for field in (
        "title",
        "content_text",
        "author_name",
        "author_id",
        "author_url",
    ):
        value = getattr(dedicated, field)
        if value is None:
            continue
        setattr(merged, field, value)
        source = dedicated.field_sources.get(field)
        if source is not None:
            merged.field_sources[field] = source
            merged.field_confidences[field] = (
                dedicated.field_confidences.get(field, 0.9)
            )


def restore_dedicated_time(merged: PageData, dedicated: PageData) -> None:
    if dedicated.published_at is None:
        return
    merged.published_at = dedicated.published_at
    merged.published_at_raw = dedicated.published_at_raw
    for field in ("published_at", "published_at_raw"):
        source = dedicated.field_sources.get(field)
        if source is None:
            continue
        merged.field_sources[field] = source
        merged.field_confidences[field] = (
            dedicated.field_confidences.get(field, 0.9)
        )
