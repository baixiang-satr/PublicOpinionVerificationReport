from datetime import datetime

from src.utils.time_utils import as_excel_datetime, parse_published_at, parse_web_published_at


def test_parse_published_at_accepts_template_date_variants() -> None:
    value = parse_published_at("2026/07/14 18:48:00")

    assert value == datetime(2026, 7, 14, 18, 48, tzinfo=value.tzinfo)
    assert as_excel_datetime(value) == datetime(2026, 7, 14, 18, 48)


def test_parse_web_time_supports_iso_unix_and_chinese_relative_values() -> None:
    reference = parse_published_at("2026-07-28 12:00:00")
    assert reference is not None

    assert parse_web_published_at("2026年07月28日 10:30:00") == parse_published_at("2026-07-28 10:30:00")
    assert parse_web_published_at("30分钟前", now=reference) == parse_published_at("2026-07-28 11:30:00")
    assert parse_web_published_at("昨天 08:15", now=reference) == parse_published_at("2026-07-27 08:15:00")
    assert parse_web_published_at(1785204000) is not None


def test_parse_web_time_extracts_absolute_time_from_label_text() -> None:
    parsed = parse_web_published_at("发布时间：2026/07/28 10:30 来源：中国日报")

    assert parsed == parse_published_at("2026-07-28 10:30:00")


def test_parse_web_time_rejects_implausible_numeric_ids() -> None:
    assert parse_web_published_at("9999999999") is None
