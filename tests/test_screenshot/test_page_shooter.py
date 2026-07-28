from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.platform_catalog import find_platform
from src.screenshot.page_shooter import PageShooter, PageScreenshotError


class FakeScreenshotPage:
    def __init__(self, *, width: int = 1_440, height: int = 900) -> None:
        self.width = width
        self.height = height
        self.options: dict[str, object] | None = None
        self.wait_scripts: list[str] = []

    async def wait_for_function(self, script: str, **_options: object) -> None:
        self.wait_scripts.append(script)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def evaluate(self, script: str) -> object:
        if "root?.scrollHeight" in script:
            return {"width": self.width, "height": self.height}
        return None

    async def screenshot(self, **options: object) -> None:
        self.options = options
        Path(str(options["path"])).write_bytes(b"image")


class EmptyScreenshotPage(FakeScreenshotPage):
    async def wait_for_function(self, script: str, **_options: object) -> None:
        self.wait_scripts.append(script)
        raise TimeoutError("content did not render")

    async def evaluate(self, script: str) -> object:
        if "const visibleText" in script:
            return False
        return await super().evaluate(script)


@pytest.mark.asyncio
async def test_long_full_page_screenshot_is_clipped_and_compressed(tmp_path: Path) -> None:
    page = FakeScreenshotPage(height=50_000)
    config = TaskConfig(
        screenshot_format="jpeg",
        full_page_screenshot=True,
        max_full_page_screenshot_height=4_096,
        screenshot_jpeg_quality=90,
        long_page_jpeg_quality=82,
    )

    path = await PageShooter(config).capture(page, 1, tmp_path)

    assert path.name == "001.jpg"
    assert page.options is not None
    assert page.options["full_page"] is False
    assert page.options["quality"] == 82
    assert page.options["clip"] == {
        "x": 0,
        "y": 0,
        "width": 1_440,
        "height": 4_096,
    }


@pytest.mark.asyncio
async def test_normal_page_keeps_full_page_mode_and_waits_for_platform_content(
    tmp_path: Path,
) -> None:
    page = FakeScreenshotPage(height=2_000)
    config = TaskConfig(screenshot_format="jpeg", full_page_screenshot=True)
    definition = find_platform("https://item.jd.com/100.html")
    assert definition is not None

    await PageShooter(config).capture(
        page,
        2,
        tmp_path,
        definition=definition,
    )

    assert page.options is not None
    assert page.options["full_page"] is True
    assert page.options["quality"] == config.screenshot_jpeg_quality
    assert "clip" not in page.options
    assert any(".sku-name" in script for script in page.wait_scripts)


@pytest.mark.asyncio
async def test_screenshot_rejects_page_without_visible_content(tmp_path: Path) -> None:
    page = EmptyScreenshotPage()

    with pytest.raises(PageScreenshotError, match="did not become visibly rendered"):
        await PageShooter(TaskConfig()).capture(page, 3, tmp_path)

    assert page.options is None
    assert not list(tmp_path.iterdir())
