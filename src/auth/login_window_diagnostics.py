"""Stability diagnostics for the human-operated interactive login window.

Some sites (mp.weixin.qq.com above all) reload themselves when their
bootstrap scripts break, which looks like a constant white flash and makes
scanning the QR code impossible.  These counters make a page-driven reload
loop visible in the application log so support can tell it apart from a
plain repaint flicker.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["LoginWindowDiagnostics", "log_window_bounds"]


class LoginWindowDiagnostics:
    """Count main-frame navigations and page errors for one login window."""

    def __init__(self, platform_key: str) -> None:
        self._platform_key = platform_key
        self.main_frame_navigations = 0
        self.page_errors = 0
        self.console_errors = 0

    def attach(self, page: Any) -> None:
        """Subscribe to page events; diagnostics must never break login."""

        on = getattr(page, "on", None)
        if not callable(on):
            return
        try:
            page.on("framenavigated", self._on_frame_navigated)
            page.on("pageerror", self._on_page_error)
            page.on("console", self._on_console_message)
        except Exception:  # noqa: BLE001 - a half-stubbed page must not raise
            return

    def log_summary(self) -> None:
        logger.info(
            "login window diagnostics platform=%s main_frame_navigations=%d "
            "page_errors=%d console_errors=%d",
            self._platform_key,
            self.main_frame_navigations,
            self.page_errors,
            self.console_errors,
        )

    def _on_frame_navigated(self, frame: Any) -> None:
        try:
            if getattr(frame, "parent_frame", None) is not None:
                return
        except Exception:  # noqa: BLE001 - treat unknown frames as top-level
            pass
        self.main_frame_navigations += 1
        if (
            self.main_frame_navigations in (2, 3)
            or self.main_frame_navigations % 5 == 0
        ):
            logger.warning(
                "login page navigated %d times (platform=%s, url=%s); "
                "a reload loop looks like a constant white flash",
                self.main_frame_navigations,
                self._platform_key,
                getattr(frame, "url", "?"),
            )

    def _on_page_error(self, _error: Any) -> None:
        self.page_errors += 1

    def _on_console_message(self, message: Any) -> None:
        try:
            if getattr(message, "type", None) == "error":
                self.console_errors += 1
        except Exception:  # noqa: BLE001 - a malformed message is not an error
            return


async def log_window_bounds(page: Any, *, label: str) -> None:
    """Log the OS window bounds so off-screen clamping issues are visible."""

    context = getattr(page, "context", None)
    if context is None or not hasattr(context, "new_cdp_session"):
        return
    session = None
    try:
        session = await context.new_cdp_session(page)
        window = await session.send("Browser.getWindowForTarget")
        logger.info("login window bounds %s: %s", label, window.get("bounds"))
    except Exception:  # noqa: BLE001 - diagnostics must never break login
        return
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception:  # noqa: BLE001 - a closed session is fine
                pass
