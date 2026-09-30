"""The one visible, human-operated interactive login window.

The login window deliberately differs from the crawl/screenshot browsers:

* It is pinned to the bundled Chromium shipped in ``ms-playwright/`` —
  ``browser_channel``/``POR_BROWSER_CHANNEL`` only steer the automated
  browsers.  Every machine therefore runs the byte-identical browser.
* It keeps software rendering (``--disable-gpu``) and a fixed device scale
  factor: login pages never need proprietary video decode, and dropping the
  GPU removes the driver/DPI-dependent white-flash repaint that made
  mp.weixin.qq.com unusable on some machines.
* The stealth init script is NOT injected.  A human logs in on the
  platform's official login URL, where anti-detection gains nothing, and
  the injected script is the prime suspect for the reload loop WeChat's
  login page fell into on some machines.

The first paint is staged off-screen and revealed once via CDP; when the
reveal fails on a CDP-capable browser the whole window is relaunched as a
plain visible window — a brief about:blank beats a login that cannot start.
"""

from __future__ import annotations

import logging
from typing import Any

from src.auth.login_window_diagnostics import (
    LoginWindowDiagnostics,
    log_window_bounds,
)
from src.auth.probe_helpers import (
    _activate_login_trigger,
    _close_quietly,
    _navigate_login,
)
from src.auth.window_visibility import reveal_window_once, stage_window_offscreen
from src.config.settings import TaskConfig
from src.screenshot.browser_options import (
    browser_context_options,
    interactive_login_launch_options,
)

logger = logging.getLogger(__name__)

__all__ = ["open_login_browser"]


async def open_login_browser(
    playwright: Any,
    config: TaskConfig,
    login_url: str,
    *,
    open_login_trigger: bool,
    diagnostics: LoginWindowDiagnostics | None = None,
) -> tuple[Any, Any, Any]:
    """Launch the pinned login browser and return ``(browser, context, page)``.

    Exactly one browser process is created per call unless the CDP reveal
    fails, in which case the off-screen browser is discarded and relaunched
    as a plainly visible window so the user can always log in.
    """

    staged_options = stage_window_offscreen(
        interactive_login_launch_options(config),
        width=config.viewport_width,
        height=config.viewport_height,
    )
    browser = await playwright.chromium.launch(**staged_options)
    try:
        context, page = await _new_login_page(browser, config, diagnostics)
        await _render_login_page(
            page,
            login_url,
            config,
            open_login_trigger=open_login_trigger,
        )
    except Exception:
        await _close_quietly(browser)
        raise
    revealed = await reveal_window_once(
        page,
        width=config.viewport_width,
        height=config.viewport_height,
    )
    if revealed or not _cdp_capable(context):
        # Without CDP there is no window to reposition (tests, exotic
        # drivers): keep whatever window the browser created.
        if revealed:
            await log_window_bounds(page, label="after-reveal")
        return browser, context, page
    logger.warning(
        "Interactive login reveal failed; relaunching a plain visible window."
    )
    await _close_quietly(browser)
    browser = await playwright.chromium.launch(
        **interactive_login_launch_options(config)
    )
    context, page = await _new_login_page(browser, config, diagnostics)
    await _render_login_page(
        page,
        login_url,
        config,
        open_login_trigger=open_login_trigger,
    )
    try:
        await page.bring_to_front()
    except Exception:  # noqa: BLE001 - the OS window is visible anyway
        pass
    return browser, context, page


async def _new_login_page(
    browser: Any,
    config: TaskConfig,
    diagnostics: LoginWindowDiagnostics | None,
) -> tuple[Any, Any]:
    context = await browser.new_context(**browser_context_options(config))
    page = await context.new_page()
    if diagnostics is not None:
        diagnostics.attach(page)
    return context, page


async def _render_login_page(
    page: Any,
    login_url: str,
    config: TaskConfig,
    *,
    open_login_trigger: bool,
) -> None:
    # A login action opens exactly one page for exactly the selected
    # platform.  Content probe URLs are reserved for the later, hidden
    # fresh-context validation.
    await _navigate_login(page, login_url, config)
    if open_login_trigger:
        for _attempt in range(12):
            if await _activate_login_trigger(page):
                break
            await page.wait_for_timeout(500)


def _cdp_capable(context: Any) -> bool:
    return hasattr(context, "new_cdp_session")
