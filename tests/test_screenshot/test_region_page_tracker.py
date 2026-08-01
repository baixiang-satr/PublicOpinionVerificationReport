from __future__ import annotations

import asyncio

import pytest

from src.screenshot.region_capture_helpers import _CaptureState
from src.screenshot.region_page_tracker import track_active_browse_page


class FocusPage:
    def __init__(self, focused: bool) -> None:
        self.focused = focused

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, _script: str) -> bool:
        return self.focused


class FakeContext:
    def __init__(self, *pages: FocusPage) -> None:
        self.pages = list(pages)


@pytest.mark.asyncio
async def test_tracker_follows_manually_opened_profile_tab() -> None:
    original = FocusPage(False)
    profile = FocusPage(True)
    state = _CaptureState(
        context=FakeContext(original, profile),
        browse_page=original,
    )
    done: asyncio.Future[object] = asyncio.get_running_loop().create_future()

    tracker = asyncio.create_task(track_active_browse_page(state, done))
    await asyncio.sleep(0.3)
    done.set_result(object())
    await tracker

    assert state.browse_page is profile
