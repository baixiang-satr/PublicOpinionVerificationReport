"""Shared helpers for dedicated platform extractors.

Keeps the individual platform modules small: JSON evaluation on the live
page, HTML stripping, epoch conversion and field application with the right
:class:`ExtractionSource` confidence tags.
"""
from __future__ import annotations

from datetime import datetime
from html import unescape
import json
import re
from typing import Any

from src.crawler.field_resolver import consider_field
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import DEFAULT_TIMEZONE

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


async def evaluate_json(page: Any, script: str) -> Any | None:
    """Evaluate *script* on the page and JSON-parse a string result."""

    try:
        raw = await page.evaluate(script)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None
    return None


async def evaluate_value(page: Any, script: str) -> Any:
    """Evaluate *script* and return the raw value, ``None`` on failure."""

    try:
        return await page.evaluate(script)
    except Exception:
        return None


def strip_html(value: str, *, max_chars: int = 100_000) -> str:
    """Convert HTML-ish content to readable plain text."""

    text = _SCRIPT_STYLE_RE.sub(" ", value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h\d|section|blockquote)>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _BLANK_LINES_RE.sub("\n\n", text).strip()
    return text[:max_chars]


def epoch_to_datetime(value: float | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, DEFAULT_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return None


def apply_json_fields(
    data: PageData,
    fields: dict[str, Any],
    *,
    source: ExtractionSource = ExtractionSource.EMBEDDED_JSON,
) -> int:
    """Apply candidate values through :func:`consider_field`; return count."""

    applied = 0
    for field, value in fields.items():
        if field == "published_at_dt" or value is None:
            continue
        if consider_field(data, field, value, source):
            applied += 1
    published = fields.get("published_at_dt")
    if isinstance(published, datetime) and data.published_at is None:
        data.published_at = published
        data.field_sources["published_at"] = source
        data.field_confidences["published_at"] = 1.0 if source is ExtractionSource.MANUAL else 0.9
        applied += 1
    return applied


def found_any(data: PageData, *fields: str) -> bool:
    """Whether any of *fields* carries a value (i.e. the extractor matched)."""

    return any(getattr(data, field) for field in fields)
