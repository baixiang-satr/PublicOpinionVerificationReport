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


@pytest.mark.asyncio
async def test_has_rendered_content_is_bounded_when_renderer_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """渲染线程卡死时 evaluate 永不返回，探测必须有界（反爬卡死回归）。"""

    from src.crawler.navigation import _has_rendered_content

    monkeypatch.setattr(
        "src.crawler.navigation._EVALUATE_TIMEOUT_SECONDS", 0.1
    )

    class HangingEvaluatePage:
        async def evaluate(self, *_args: object, **_kwargs: object) -> None:
            await asyncio.Event().wait()

    result = await asyncio.wait_for(
        _has_rendered_content(HangingEvaluatePage()),
        timeout=2,
    )
    assert result is False
