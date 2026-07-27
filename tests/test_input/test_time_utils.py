from datetime import datetime

from src.utils.time_utils import as_excel_datetime, parse_published_at


def test_parse_published_at_accepts_template_date_variants() -> None:
    value = parse_published_at("2026/07/14 18:48:00")

    assert value == datetime(2026, 7, 14, 18, 48, tzinfo=value.tzinfo)
    assert as_excel_datetime(value) == datetime(2026, 7, 14, 18, 48)
