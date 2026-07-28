"""Bounded rendered-page navigation and stabilization helpers."""

from __future__ import annotations

import math
import random
from typing import Any

from src.domain.models import TaskError


async def navigate_page(
    page: Any,
    url: str,
    timeout_milliseconds: int,
) -> tuple[Any, TaskError | None]:
    """Navigate without treating a usable, partially loaded DOM as a failure."""

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_milliseconds,
        )
    except Exception as error:
        if not _is_timeout_error(error) or not await _has_rendered_content(page):
            raise
        return (
            None,
            TaskError(
                "navigation",
                "NAVIGATION_PARTIAL_TIMEOUT",
                "页面未完全结束加载，但已取得可读取内容，继续采集。",
                retryable=False,
            ),
        )

    if hasattr(page, "wait_for_load_state"):
        try:
            await page.wait_for_load_state(
                "load",
                timeout=min(5_000, max(1_000, timeout_milliseconds // 3)),
            )
        except Exception:
            pass
    return response, None


async def stabilize_rendered_page(
    page: Any,
    stabilize_milliseconds: int,
    *,
    definition: Any = None,
) -> None:
    """Trigger bounded lazy loading, wait briefly for platform content, then
    wait for images to finish loading."""

    await _wait_for_platform_marker(page, definition)
    await _scroll_for_lazy_content(page, stabilize_milliseconds)
    await _wait_for_images_loaded(page)


async def _wait_for_platform_marker(page: Any, definition: Any) -> None:
    if definition is None or not hasattr(page, "locator"):
        return
    selectors = [
        selector
        for field in ("content_text", "title")
        for selector in definition.selectors.get(field, ())
    ]
    if not selectors:
        return
    try:
        await page.locator(", ".join(selectors)).first.wait_for(
            state="visible",
            timeout=3_000,
        )
    except Exception:
        pass


async def _scroll_for_lazy_content(page: Any, stabilize_milliseconds: int) -> None:
    try:
        scroll_height = int(await page.evaluate("document.body.scrollHeight") or 0)
        viewport_height = int(await page.evaluate("window.innerHeight") or 0)
        if viewport_height <= 0:
            return
        if scroll_height <= viewport_height:
            if stabilize_milliseconds:
                await page.wait_for_timeout(stabilize_milliseconds)
            return
        steps = min(8, max(3, math.ceil(scroll_height / viewport_height)))
        for index in range(1, steps + 1):
            current_height = int(await page.evaluate("document.body.scrollHeight") or scroll_height)
            max_scroll = max(0, current_height - viewport_height)
            scroll_to = int(max_scroll * index / steps)
            await page.evaluate(f"window.scrollTo(0, {scroll_to})")
            await page.wait_for_timeout(random.randint(200, 500))
        await page.wait_for_timeout(max(stabilize_milliseconds, 800))
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(random.randint(100, 300))
    except Exception:
        if stabilize_milliseconds:
            try:
                await page.wait_for_timeout(stabilize_milliseconds)
            except Exception:
                pass


async def _wait_for_images_loaded(page: Any) -> None:
    """Best-effort wait for images to finish loading after lazy scroll."""
    if not hasattr(page, "evaluate"):
        return
    try:
        await page.wait_for_function(
            """() =>
                Array.from(document.images).every(img => img.complete)
            """,
            timeout=4_000,
        )
    except Exception:
        pass


async def _has_rendered_content(page: Any) -> bool:
    if not hasattr(page, "evaluate"):
        return False
    try:
        length = await page.evaluate(
            "() => (document.body?.innerText || document.body?.textContent || '').trim().length"
        )
        return int(length or 0) >= 12
    except Exception:
        return False


def _is_timeout_error(error: Exception) -> bool:
    return "timeout" in type(error).__name__.casefold() or "timeout" in str(error).casefold()
