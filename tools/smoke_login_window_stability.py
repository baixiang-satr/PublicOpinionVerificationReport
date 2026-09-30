"""Real-browser smoke: the pinned interactive login window must not flicker.

Opens the WeChat Official Accounts login page through the production
login-window path and watches main-frame navigations for 90 seconds.  A
healthy window navigates at most twice (initial commit plus one redirect);
a reload loop means the page is fighting the environment and the operator
sees a constant white flash.  Run manually:

    python tools/smoke_login_window_stability.py
"""

from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from src.auth.login_window import open_login_browser
from src.auth.login_window_diagnostics import LoginWindowDiagnostics
from src.config.settings import TaskConfig

_WATCH_MILLISECONDS = 90_000
_MAX_MAIN_FRAME_NAVIGATIONS = 2


async def main() -> None:
    config = TaskConfig()
    diagnostics = LoginWindowDiagnostics("wechat_official")
    async with async_playwright() as playwright:
        browser, context, page = await open_login_browser(
            playwright,
            config,
            "https://mp.weixin.qq.com/",
            open_login_trigger=False,
            diagnostics=diagnostics,
        )
        try:
            await page.wait_for_timeout(_WATCH_MILLISECONDS)
        finally:
            await context.close()
            await browser.close()
    diagnostics.log_summary()
    assert (
        diagnostics.main_frame_navigations <= _MAX_MAIN_FRAME_NAVIGATIONS
    ), (
        "login page reloaded "
        f"{diagnostics.main_frame_navigations} times in "
        f"{_WATCH_MILLISECONDS // 1000}s — reload loop not fixed"
    )
    print(
        "PASS: login window stable; "
        f"main_frame_navigations={diagnostics.main_frame_navigations}"
    )


if __name__ == "__main__":
    asyncio.run(main())
