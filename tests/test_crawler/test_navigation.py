import asyncio

import pytest

from src.crawler.navigation import navigate_page


class HangingPage:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def goto(self, *_args: object, **_kwargs: object):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_navigation_is_interrupted_immediately_by_cancel_event() -> None:
    page = HangingPage()
    cancel_event = asyncio.Event()
    operation = asyncio.create_task(
        navigate_page(page, "https://example.test", 60_000, cancel_event)
    )
    await page.started.wait()

    cancel_event.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=1)
    assert page.cancelled
