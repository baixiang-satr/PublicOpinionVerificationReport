from datetime import datetime

from src.utils.time_utils import (
    as_excel_datetime,
    parse_published_at,
    parse_published_at_from_text,
    parse_web_published_at,
    pick_latest_published_at,
)


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


def test_parse_web_time_supports_chinese_month_day_without_year() -> None:
    reference = parse_published_at("2026-09-21 15:00:00")

    assert parse_web_published_at("9月21日 10:00", now=reference) == parse_published_at("2026-09-21 10:00:00")
    assert parse_web_published_at("09-21 10:00", now=reference) == parse_published_at("2026-09-21 10:00:00")


def test_parse_published_at_from_text_picks_latest_fragment() -> None:
    reference = parse_published_at("2026-09-21 15:00:00")

    assert parse_published_at_from_text(
        "编辑于 2026-09-19 08:00 发布于 2026-09-20 10:00", now=reference
    ) == parse_published_at("2026-09-20 10:00:00")
    assert parse_published_at_from_text(
        "发布于 9月21日 09:15 来自微博", now=reference
    ) == parse_published_at("2026-09-21 09:15:00")
    assert parse_published_at_from_text("满300-50 限时优惠", now=reference) is None
    assert parse_published_at_from_text("00:00 / 01:00", now=reference) is None


def test_pick_latest_published_at_prefers_freshest_in_post_candidate() -> None:
    reference = parse_published_at("2026-09-21 15:00:00")

    latest = pick_latest_published_at(
        ["2024-05-01 08:00", "9月21日 10:00", "2026-09-20 09:30:00"],
        now=reference,
    )

    assert latest == parse_published_at("2026-09-21 10:00:00")


def test_pick_latest_published_at_skips_comment_region_when_in_post_exists() -> None:
    reference = parse_published_at("2026-09-21 15:00:00")

    latest = pick_latest_published_at(
        [("9月21日 10:00", False), ("2026-09-21 14:59", True)],
        now=reference,
    )

    assert latest == parse_published_at("2026-09-21 10:00:00")


def test_pick_latest_published_at_falls_back_to_comment_only_candidates() -> None:
    reference = parse_published_at("2026-09-21 15:00:00")

    latest = pick_latest_published_at(
        [("2026-09-20 08:00", True), ("2026-09-21 12:00", True)],
        now=reference,
    )

    assert latest == parse_published_at("2026-09-21 12:00:00")


def test_pick_latest_published_at_rejects_far_future_and_unparseable() -> None:
    reference = parse_published_at("2026-09-21 15:00:00")

    assert pick_latest_published_at(["2099-01-01 00:00"], now=reference) is None
    assert pick_latest_published_at([], now=reference) is None
    assert pick_latest_published_at([None, "不是时间"], now=reference) is None
