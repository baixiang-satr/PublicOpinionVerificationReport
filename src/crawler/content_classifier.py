"""Classify page content and merge OCR text without inventing evidence."""

from __future__ import annotations

import re

from src.domain.models import (
    ContentKind,
    ExtractionSource,
    OcrStatus,
    PageData,
)
from src.ocr.models import OcrBatchResult


IMAGE_ONLY_NO_TEXT_MARKER = "【纯图片内容：图片中未识别到文字】"
IMAGE_TEXT_HEADING = "【图片文字】"


def apply_image_ocr(
    page: PageData,
    result: OcrBatchResult,
    *,
    summary_max_chars: int,
) -> None:
    """Apply a completed image OCR batch to content fields and metrics."""

    page.ocr_status = result.status
    page.ocr_image_count = len(result.images)
    page.ocr_text_image_count = result.text_image_count
    page.ocr_text = result.text or None
    existing = (page.content_text or "").strip()

    if result.status == OcrStatus.SUCCESS and result.text.strip():
        image_text = _novel_lines(existing, result.text)
        if existing:
            if image_text:
                page.content_text = (
                    f"{existing}\n{IMAGE_TEXT_HEADING}\n{image_text}"
                )
                page.content_kind = ContentKind.MIXED_TEXT_AND_IMAGE
                page.field_sources["image_text"] = ExtractionSource.OCR
            else:
                page.content_text = existing
                page.content_kind = ContentKind.TEXT
        else:
            page.content_text = result.text.strip()
            page.content_kind = ContentKind.IMAGE_WITH_TEXT
            page.field_sources["content_text"] = ExtractionSource.OCR
    elif result.status == OcrStatus.NO_TEXT:
        if existing:
            page.content_text = existing
            page.content_kind = ContentKind.TEXT
        elif result.images:
            page.content_text = IMAGE_ONLY_NO_TEXT_MARKER
            page.content_kind = ContentKind.IMAGE_WITHOUT_TEXT
            page.field_sources["content_text"] = ExtractionSource.SYSTEM_MARKER
    elif existing:
        page.content_text = existing
        page.content_kind = ContentKind.TEXT

    update_content_metrics(page, summary_max_chars=summary_max_chars)


def initialize_content_kind(
    page: PageData,
    *,
    summary_max_chars: int,
) -> None:
    if page.content_text and page.content_kind == ContentKind.UNKNOWN:
        page.content_kind = ContentKind.TEXT
    update_content_metrics(page, summary_max_chars=summary_max_chars)


def update_content_metrics(
    page: PageData,
    *,
    summary_max_chars: int,
) -> None:
    content = page.content_text or ""
    page.original_content_chars = len(content)
    page.content_summary = content[:summary_max_chars]
    page.summary_truncated = len(content) > summary_max_chars


def _novel_lines(existing: str, recognized: str) -> str:
    existing_lines = {
        _line_key(line)
        for line in existing.splitlines()
        if _line_key(line)
    }
    output: list[str] = []
    seen = set(existing_lines)
    for raw in recognized.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        key = _line_key(line)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(line)
    return "\n".join(output)


def _line_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()
