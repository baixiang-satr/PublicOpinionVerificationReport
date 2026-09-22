"""Published-time parsing and Excel-compatible local datetime conversion."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone as fixed_timezone, tzinfo
from email.utils import parsedate_to_datetime
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    DEFAULT_TIMEZONE: tzinfo = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    DEFAULT_TIMEZONE = fixed_timezone(timedelta(hours=8), name="Asia/Shanghai")
DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
)


def parse_published_at(value: str | datetime | None, timezone: tzinfo = DEFAULT_TIMEZONE) -> datetime | None:
    """Parse common template date strings into timezone-aware local datetimes."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
    text = str(value).strip()
    for date_format in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=timezone)
        except ValueError:
            continue
    raise ValueError(f"Unsupported published time: {value!r}")


def as_excel_datetime(value: datetime | None, timezone: tzinfo = DEFAULT_TIMEZONE) -> datetime | None:
    """Return a naive local datetime, which Excel COM writes as a native Excel date."""

    if value is None:
        return None
    local_value = value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
    return local_value.replace(tzinfo=None, microsecond=0)


def parse_web_published_at(
    value: str | int | float | datetime | None,
    *,
    now: datetime | None = None,
    timezone: tzinfo = DEFAULT_TIMEZONE,
) -> datetime | None:
    """Parse ISO, Unix, Chinese absolute and common relative web timestamps."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return parse_published_at(value, timezone)
    reference = now or datetime.now(timezone)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone)
    if isinstance(value, (int, float)) or re.fullmatch(r"\d{10,13}", str(value).strip()):
        try:
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            parsed = datetime.fromtimestamp(timestamp, timezone)
        except (OSError, OverflowError, ValueError):
            return None
        if not datetime(1990, 1, 1, tzinfo=timezone) <= parsed <= reference + timedelta(days=2):
            return None
        return parsed
    text = str(value).strip()
    try:
        return parse_published_at(datetime.fromisoformat(text.replace("Z", "+00:00")), timezone)
    except ValueError:
        pass
    try:
        rfc_value = parsedate_to_datetime(text)
        if rfc_value is not None:
            return parse_published_at(rfc_value, timezone)
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = text.replace("年", "-").replace("月", "-").replace("日", " ").replace("T", " ").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    try:
        return parse_published_at(normalized, timezone)
    except ValueError:
        pass
    absolute_match = re.search(
        r"(?<!\d)((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})"
        r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?(?!\d)",
        normalized,
    )
    if absolute_match:
        try:
            return datetime(
                year=int(absolute_match.group(1)),
                month=int(absolute_match.group(2)),
                day=int(absolute_match.group(3)),
                hour=int(absolute_match.group(4) or 0),
                minute=int(absolute_match.group(5) or 0),
                second=int(absolute_match.group(6) or 0),
                tzinfo=timezone,
            )
        except ValueError:
            pass
    if text in {"刚刚", "刚才"}:
        return reference.replace(microsecond=0)
    for pattern, unit in ((r"(\d+)\s*分钟前", "minutes"), (r"(\d+)\s*小时前", "hours"), (r"(\d+)\s*天前", "days")):
        match = re.fullmatch(pattern, text)
        if match:
            return (reference - timedelta(**{unit: int(match.group(1))})).replace(microsecond=0)
    relative_match = re.fullmatch(r"(今天|昨天)\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if relative_match:
        target = reference - timedelta(days=0 if relative_match.group(1) == "今天" else 1)
        return target.replace(
            hour=int(relative_match.group(2)),
            minute=int(relative_match.group(3)),
            second=int(relative_match.group(4) or 0),
            microsecond=0,
        )
    # 归一化文本匹配：微博当年发布时间显示为「9月21日 10:00」（无年份），
    # 归一化后为「9-21 10:00」；直接对原文匹配会漏掉中文月日格式。
    month_day_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", normalized)
    if month_day_match:
        try:
            return reference.replace(
                month=int(month_day_match.group(1)),
                day=int(month_day_match.group(2)),
                hour=int(month_day_match.group(3) or 0),
                minute=int(month_day_match.group(4) or 0),
                second=0,
                microsecond=0,
            )
        except ValueError:
            return None
    return None


_MAX_FUTURE_DRIFT = timedelta(days=2)

# 长文本（OCR 行/DOM 元素文本）中内嵌的发布日期片段：带年份的绝对日期、
# 微博当年帖的无年份「M月D日」，以及「今天/昨天 HH:MM」相对时间。
_DATE_FRAGMENT_PATTERN = re.compile(
    r"(?<!\d)(?:19|20)\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?(?!\d)"
    r"|(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s+\d{1,2}:\d{2})?"
    r"|(?<!\d)\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?(?!\d)"
    r"|(?:今天|昨天)\s*\d{1,2}:\d{2}(?::\d{2})?"
)


def parse_published_at_from_text(
    value: str | int | float | datetime | None,
    *,
    now: datetime | None = None,
    timezone: tzinfo = DEFAULT_TIMEZONE,
) -> datetime | None:
    """Parse the freshest publish time embedded in free text.

    A single element/OCR line may carry several dates（标签、被转发原帖、推荐
    卡片），因此所有日期片段都参与解析，最新者胜出；相对时间（"1小时前"）
    与纯时间戳等整串格式同样兼容。
    """

    reference = now or datetime.now(timezone)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone)
    candidates = [parse_web_published_at(value, now=reference, timezone=timezone)]
    if isinstance(value, str):
        candidates.extend(
            parse_web_published_at(match.group(0), now=reference, timezone=timezone)
            for match in _DATE_FRAGMENT_PATTERN.finditer(value)
        )
    parsed = [candidate for candidate in candidates if candidate is not None]
    return max(parsed) if parsed else None


def _candidate_parts(candidate: Any) -> tuple[Any, bool]:
    if isinstance(candidate, tuple) and len(candidate) == 2:
        return candidate[0], bool(candidate[1])
    return candidate, False


def select_latest_published_at(
    candidates: Iterable[Any],
    *,
    now: datetime | None = None,
    timezone: tzinfo = DEFAULT_TIMEZONE,
) -> tuple[datetime, Any] | None:
    """Return ``(parsed, raw_candidate)`` closest to now from one source.

    Candidates are strings/datetimes, or ``(value, in_comment)`` tuples where
    ``in_comment`` marks matches inside comment/recommendation containers.
    评论区候选仅在无主帖区候选可解析时才启用；远未来（>now+2天）一律剔除。
    """

    reference = now or datetime.now(timezone)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone)
    in_post: list[tuple[datetime, Any]] = []
    any_region: list[tuple[datetime, Any]] = []
    for candidate in candidates:
        value, in_comment = _candidate_parts(candidate)
        parsed = parse_published_at_from_text(value, now=reference, timezone=timezone)
        if parsed is None or parsed > reference + _MAX_FUTURE_DRIFT:
            continue
        entry = (parsed, value)
        any_region.append(entry)
        if not in_comment:
            in_post.append(entry)
    pool = in_post or any_region
    return max(pool, key=lambda item: item[0]) if pool else None


def pick_latest_published_at(
    candidates: Iterable[Any],
    *,
    now: datetime | None = None,
    timezone: tzinfo = DEFAULT_TIMEZONE,
) -> datetime | None:
    """Return the freshest plausible publish time among ``candidates``."""

    selected = select_latest_published_at(candidates, now=now, timezone=timezone)
    return selected[0] if selected else None
