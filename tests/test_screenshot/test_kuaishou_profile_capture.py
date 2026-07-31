"""Kuaishou manual profile capture context regressions."""

from src.screenshot.region_capture import _uses_desktop_profile_context


def test_only_kuaishou_author_capture_uses_desktop_context() -> None:
    assert _uses_desktop_profile_context("kuaishou", "author")
    assert not _uses_desktop_profile_context("kuaishou", "content")
    assert not _uses_desktop_profile_context("douyin", "author")
