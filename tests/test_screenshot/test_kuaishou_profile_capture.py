"""Kuaishou manual profile capture context regressions."""

from src.screenshot.region_capture_helpers import uses_desktop_profile_context


def test_only_kuaishou_author_capture_uses_desktop_context() -> None:
    assert uses_desktop_profile_context("kuaishou", "author")
    assert not uses_desktop_profile_context("kuaishou", "content")
    assert not uses_desktop_profile_context("douyin", "author")
