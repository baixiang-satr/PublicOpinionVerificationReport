"""Track the browser tab an operator actually uses during manual capture."""

from __future__ import annotations

import asyncio
from typing import Any

from src.screenshot.region_capture_helpers import _CaptureState, page_is_closed


async def track_active_browse_page(
    state: _CaptureState,
    done: asyncio.Future[Any],
) -> None:
    """Follow the tab focused after manual navigation or a target=_blank link."""

    while not done.done():
        if state.select_pending:
            # The selection tab is being created: it must not be adopted as
            # the browse page before its state reference is assigned.
            await asyncio.sleep(0.25)
            continue
        pages = list(getattr(state.context, "pages", ()) or ())
        for candidate in reversed(pages):
            if candidate is state.select_page or page_is_closed(candidate):
                continue
            try:
                focused = bool(await candidate.evaluate("() => document.hasFocus()"))
            except Exception:  # noqa: BLE001 - keep checking other live tabs
                focused = False
            if focused:
                state.browse_page = candidate
                break
        await asyncio.sleep(0.25)
