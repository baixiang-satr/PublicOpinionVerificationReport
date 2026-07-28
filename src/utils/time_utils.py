"""Published-time parsing and Excel-compatible local datetime conversion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as fixed_timezone, tzinfo
from email.utils import parsedate_to_datetime
import re
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
    month_day_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if month_day_match:
        return reference.replace(
            month=int(month_day_match.group(1)),
            day=int(month_day_match.group(2)),
            hour=int(month_day_match.group(3) or 0),
            minute=int(month_day_match.group(4) or 0),
            second=0,
            microsecond=0,
        )
    return None
