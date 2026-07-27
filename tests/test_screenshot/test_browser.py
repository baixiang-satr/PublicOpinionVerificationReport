from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.screenshot.browser import BrowserPool, BrowserUnavailableError
from src.screenshot.page_shooter import PageShooter


pytestmark = [pytest.mark.asyncio, pytest.mark.playwright]


async def test_owned_browser_context_and_page_are_closed_after_screenshot(tmp_path: Path) -> None:
    config = TaskConfig(
        max_concurrency=1,
        page_timeout_seconds=10,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
        screenshot_format="png",
    )
    pool = BrowserPool(config)
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page() as page:
            await page.set_content("<main><h1>Local fixture</h1><p>Rendered content</p></main>")
            screenshot = await PageShooter(config).capture(page, 7, tmp_path)
            assert screenshot.name == "007.png"
            assert screenshot.read_bytes().startswith(b"\x89PNG")
    finally:
        await pool.close()

    assert not pool.is_started
