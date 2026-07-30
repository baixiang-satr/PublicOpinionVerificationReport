"""Persistent interactive capture browser: one session per app run.

The session owns a single headed browser (Edge→Chrome→Chromium fallback)
with one context per platform.  Contexts keep their live cookies in memory
for the whole app session — a login completed inside the capture window
survives every later capture — and VALID platform profiles are refreshed
back to the encrypted store on save/close so cookie rotation persists
across app restarts.

Threading rule: a session lives on ONE event loop (the capture thread).
Playwright objects must never cross loop boundaries, so every method is a
coroutine scheduled by the owning runner.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import logging
from typing import Any, Callable, Coroutine

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.screenshot.browser_options import (
    STEALTH_SCRIPT_PATH,
    browser_context_options,
    browser_launch_options,
    launch_headed_with_fallback,
)
from src.screenshot.region_capture_scripts import BINDING_NAME, OVERLAY_JS

logger = logging.getLogger(__name__)

GUEST_KEY = "_guest"

BindingHandler = Callable[[dict[str, Any], Any], Coroutine[Any, Any, None]]


class CaptureSession:
    """Owns the long-lived capture browser and its per-platform contexts."""

    def __init__(
        self,
        config: TaskConfig,
        store: AuthProfileStore | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: dict[str, Any] = {}
        self._browse_pages: dict[str, Any] = {}
        self._binding_handler: BindingHandler | None = None

    def set_binding_handler(self, handler: BindingHandler | None) -> None:
        """Route toolbar binding calls to the active capture (one at a time)."""

        self._binding_handler = handler

    async def ensure_browser(self) -> Any:
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return self._browser
            except Exception:  # noqa: BLE001 — 失联即重建
                pass
        await self._teardown_contexts()
        await self._stop_playwright()
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        config = replace(self._config, headless=False)
        launch_options = browser_launch_options(config)
        launch_options["args"] = [*launch_options.get("args", ()), "--start-maximized"]
        try:
            self._browser = await launch_headed_with_fallback(
                self._playwright,
                config,
                launch_options,
            )
        except Exception:
            await self._stop_playwright()
            raise
        return self._browser

    async def context_for(
        self,
        key: str,
        storage_state: dict[str, Any] | None,
    ) -> Any:
        """Reuse the platform's context (live cookies stay in memory)."""

        await self.ensure_browser()
        context = self._contexts.get(key)
        if context is not None:
            return context
        config = replace(self._config, headless=False)
        options = browser_context_options(config, storage_state)
        # The page must fill the whole maximized window, not a fixed viewport.
        options.pop("viewport", None)
        options.pop("device_scale_factor", None)
        options["no_viewport"] = True
        context = await self._browser.new_context(**options)
        if config.enable_stealth and STEALTH_SCRIPT_PATH.is_file():
            await context.add_init_script(path=str(STEALTH_SCRIPT_PATH))
        await context.add_init_script(script=OVERLAY_JS)
        await context.expose_binding(BINDING_NAME, self._dispatch_binding)
        self._contexts[key] = context
        return context

    async def browse_page_for(self, key: str, context: Any) -> Any:
        """Reuse the platform's browse tab so the window (and login) persists."""

        page = self._browse_pages.get(key)
        if page is not None:
            is_closed = getattr(page, "is_closed", None)
            try:
                closed = bool(is_closed()) if callable(is_closed) else False
            except Exception:  # noqa: BLE001 — 失联即新建
                closed = True
            if not closed:
                return page
        page = await context.new_page()
        self._browse_pages[key] = page
        return page

    async def save_states(self) -> None:
        """Refresh VALID platform profiles with the live (rotated) cookies."""

        if self._store is None:
            return
        for key, context in list(self._contexts.items()):
            if key == GUEST_KEY:
                continue
            try:
                profile = self._store.profile_for(key)
            except Exception:  # noqa: BLE001 — 未知平台跳过
                continue
            if profile.status != AuthStatus.VALID:
                continue
            try:
                state = await asyncio.wait_for(
                    context.storage_state(indexed_db=True),
                    timeout=3.0,
                )
            except Exception:  # noqa: BLE001 — context 已关闭则跳过
                continue
            try:
                result = AuthProbeResult(
                    platform_key=key,
                    status=AuthStatus.VALID,
                    checked_at=datetime.now().astimezone(),
                    original_url=profile.validation_url or "",
                    message="截图会话复用并刷新登录态。",
                    used_saved_state=True,
                )
                self._store.commit_validated_state(key, state, result)
            except Exception as error:  # noqa: BLE001 — 尽力而为
                logger.warning(
                    "Unable to persist capture-session state for %s: %s",
                    key,
                    error,
                )

    async def close(self) -> None:
        """Persist states and tear everything down (app shutdown)."""

        try:
            await self.save_states()
        finally:
            await self._teardown_contexts()
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:  # noqa: BLE001 — 关闭阶段尽力而为
                    pass
                self._browser = None
            await self._stop_playwright()

    async def _dispatch_binding(self, source: dict[str, Any], payload: Any) -> None:
        handler = self._binding_handler
        if handler is not None:
            await handler(source, payload)

    async def _teardown_contexts(self) -> None:
        contexts = list(self._contexts.values())
        self._contexts.clear()
        self._browse_pages.clear()
        for context in contexts:
            try:
                await context.close()
            except Exception:  # noqa: BLE001 — 已关闭则忽略
                pass

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001 — 尽力而为
                pass
            self._playwright = None
