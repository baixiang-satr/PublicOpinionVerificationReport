"""Published-time parsing and Excel-compatible local datetime conversion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as fixed_timezone, tzinfo
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
    "%Y-%m-%d",
    "%Y/%m/%d",
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
