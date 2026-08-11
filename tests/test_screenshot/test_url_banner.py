"""U06：自动截图 URL 横幅——注入/移除成对、异常路径仍移除、OCR 过滤。"""

from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.screenshot_field_recovery import (
    recover_fields_from_ocr_text,
    strip_banner_lines,
)
from src.domain.models import PageData
from src.screenshot.page_shooter import PageScreenshotError, PageShooter
from src.screenshot.url_banner_scripts import BANNER_ELEMENT_ID
from tests.test_screenshot.test_page_shooter import FakeScreenshotPage


class _BannerTrackingPage(FakeScreenshotPage):
    def __init__(self) -> None:
        super().__init__()
        self.banner_calls: list[str] = []

    async def evaluate(self, script: str, *args: object) -> object:
        if BANNER_ELEMENT_ID in script:
            self.banner_calls.append("inject" if "appendChild" in script else "remove")
            return None
        return await super().evaluate(script, *args)


class _FailingShotPage(_BannerTrackingPage):
    async def screenshot(self, **_options: object) -> None:
        raise RuntimeError("boom")


def _config() -> TaskConfig:
    return TaskConfig(screenshot_format="jpeg", full_page_screenshot=False)


@pytest.mark.asyncio
async def test_capture_injects_banner_before_screenshot_and_removes_after(
    tmp_path: Path,
) -> None:
    page = _BannerTrackingPage()

    path = await PageShooter(_config()).capture(page, 1, tmp_path)

    assert path.name == "001.jpg"
    assert page.banner_calls == ["inject", "remove"]
    assert page.options is not None  # 截图确实发生在注入与移除之间


@pytest.mark.asyncio
async def test_banner_removed_even_when_screenshot_fails(tmp_path: Path) -> None:
    page = _FailingShotPage()

    with pytest.raises(PageScreenshotError):
        await PageShooter(_config()).capture(page, 1, tmp_path)

    assert page.banner_calls == ["inject", "remove"]


def test_strip_banner_lines_removes_bare_url_lines() -> None:
    text = (
        "https://www.douyin.com/video/123\n"
        "正文第一行\n"
        "  https://example.com/x  \n"
        "正文第二行"
    )

    assert strip_banner_lines(text) == "正文第一行\n正文第二行"


def test_ocr_recovery_excludes_banner_url_but_keeps_content_and_time() -> None:
    page = PageData()

    recover_fields_from_ocr_text(
        page,
        "https://www.douyin.com/video/123456\n正文内容在这里\n2026-08-01 10:20",
        summary_max_chars=2_000,
    )

    assert page.content_text is not None
    assert "douyin.com" not in page.content_text
    assert "正文内容在这里" in page.content_text
    assert page.published_at is not None  # 时间行不受影响


def test_banner_only_ocr_text_recovers_nothing() -> None:
    page = PageData()

    recover_fields_from_ocr_text(
        page,
        "https://www.douyin.com/video/123456",
        summary_max_chars=2_000,
    )

    assert page.content_text is None
    assert page.published_at is None
