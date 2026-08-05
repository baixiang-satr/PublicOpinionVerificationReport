"""Tests for required/optional author-screenshot enforcement in optional assets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.config.settings import TaskConfig
from src.crawler.optional_assets import (
    author_screenshot_required_unmet,
    collect_optional_assets,
)
from src.domain.models import PageData, RecordResult, RecordStatus, UrlTask
from src.screenshot.author_shooter import AuthorScreenshotError


class FakeOcrPipeline:
    async def process_content_images(self, *_args: Any, **_kwargs: Any) -> list:
        return []


class FakeAuthorShooter:
    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def capture(self, *_args: Any, **_kwargs: Any) -> Path:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


def _record(author_url: str | None) -> RecordResult:
    return RecordResult(
        UrlTask(1, "https://www.douyin.com/video/1", "https://www.douyin.com/video/1"),
        RecordStatus.ROUTED,
        page=PageData(
            final_url="https://www.douyin.com/video/1",
            author_url=author_url,
            author_name="作者",
        ),
    )


async def _run(
    result: RecordResult,
    shooter: FakeAuthorShooter,
    tmp_path: Path,
    platform_key: str,
) -> None:
    await collect_optional_assets(
        config=TaskConfig(),
        author_shooter=shooter,
        asset_collector=None,
        ocr_pipeline=FakeOcrPipeline(),
        page=object(),
        result=result,
        output_dir=tmp_path,
        cancel_event=asyncio.Event(),
        platform_key=platform_key,
    )


@pytest.mark.asyncio
async def test_required_platform_retries_once_and_recovers(tmp_path: Path) -> None:
    recovered = tmp_path / "001主页.jpg"
    shooter = FakeAuthorShooter(
        AuthorScreenshotError("AUTHOR_CONTENT_NOT_READY", "未渲染"),
        recovered,
    )
    result = _record("https://www.douyin.com/user/SEC-ABC")

    await _run(result, shooter, tmp_path, "douyin")

    assert shooter.calls == 2
    assert result.assets.author_screenshot == recovered
    assert not any(
        error.code == "AUTHOR_SCREENSHOT_REQUIRED" for error in result.errors
    )
    assert not author_screenshot_required_unmet("douyin", result)


@pytest.mark.asyncio
async def test_required_platform_marks_unmet_after_double_failure(tmp_path: Path) -> None:
    shooter = FakeAuthorShooter(
        AuthorScreenshotError("AUTHOR_CONTENT_NOT_READY", "未渲染"),
        AuthorScreenshotError("AUTHOR_ACCESS_RESTRICTED", "验证页"),
    )
    result = _record("https://www.douyin.com/user/SEC-ABC")

    await _run(result, shooter, tmp_path, "douyin")

    assert shooter.calls == 2
    assert result.assets.author_screenshot is None
    assert any(
        error.code == "AUTHOR_SCREENSHOT_REQUIRED" for error in result.errors
    )
    assert author_screenshot_required_unmet("douyin", result)


@pytest.mark.asyncio
async def test_optional_platform_does_not_retry_and_stays_unmarked(tmp_path: Path) -> None:
    shooter = FakeAuthorShooter(
        AuthorScreenshotError("AUTHOR_CONTENT_NOT_READY", "未渲染"),
    )
    result = _record("https://www.douyin.com/user/SEC-ABC")

    await _run(result, shooter, tmp_path, "baijiahao")

    assert shooter.calls == 1
    assert result.assets.author_screenshot is None
    assert not any(
        error.code == "AUTHOR_SCREENSHOT_REQUIRED" for error in result.errors
    )
    assert not author_screenshot_required_unmet("baijiahao", result)


@pytest.mark.asyncio
async def test_required_platform_without_author_url_is_marked(tmp_path: Path) -> None:
    shooter = FakeAuthorShooter()
    result = _record(None)

    await _run(result, shooter, tmp_path, "wechat_official")

    assert shooter.calls == 0
    assert any(
        error.code == "AUTHOR_SCREENSHOT_REQUIRED" for error in result.errors
    )
