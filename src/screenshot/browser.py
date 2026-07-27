"""Owned Playwright Chromium lifecycle with bounded isolated contexts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from src.config.settings import TaskConfig


class BrowserUnavailableError(RuntimeError):
    """Raised when Playwright or its owned Chromium executable is unavailable."""


class BrowserPool:
    def __init__(self, config: TaskConfig) -> None:
        self._config = config
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: set[Any] = set()
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_started(self) -> bool:
        return self._browser is not None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.is_started:
                return
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self._config.headless)
            except Exception as error:
                await self.close()
                raise BrowserUnavailableError(
                    "Unable to start owned Chromium. Install it with: python -m playwright install chromium"
                ) from error

    async def close(self) -> None:
        contexts = tuple(self._contexts)
        self._contexts.clear()
        if contexts:
            await asyncio.gather(*(context.close() for context in contexts), return_exceptions=True)
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

    async def __aenter__(self) -> "BrowserPool":
        await self.start()
        return self

    async def __aexit__(self, _error_type: Any, _error: Any, _traceback: Any) -> None:
        await self.close()

    @asynccontextmanager
    async def page(self, cancel_event: asyncio.Event | None = None) -> AsyncIterator[Any]:
        await self._acquire_slot(cancel_event)
        context = None
        page = None
        try:
            if not self.is_started:
                raise BrowserUnavailableError("BrowserPool.start() must be called before opening a page.")
            context_options: dict[str, Any] = {
                "viewport": {"width": 1440, "height": 1000},
                "locale": "zh-CN",
                "timezone_id": self._config.timezone,
            }
            storage_state = self._config.storage_state_path
            if storage_state is not None:
                storage_path = Path(storage_state).expanduser().resolve()
                if not storage_path.is_file():
                    raise FileNotFoundError(f"Configured storage state does not exist: {storage_path}")
                context_options["storage_state"] = str(storage_path)
            context = await self._browser.new_context(**context_options)
            self._contexts.add(context)
            page = await context.new_page()
            page.set_default_navigation_timeout(self._config.page_timeout_seconds * 1000)
            page.set_default_timeout(self._config.page_timeout_seconds * 1000)
            yield page
        finally:
            if page is not None:
                await _close_quietly(page)
            if context is not None:
                self._contexts.discard(context)
                await _close_quietly(context)
            self._semaphore.release()

    async def _acquire_slot(self, cancel_event: asyncio.Event | None) -> None:
        if cancel_event is None:
            await self._semaphore.acquire()
            return
        if cancel_event.is_set():
            raise asyncio.CancelledError
        acquire_task = asyncio.create_task(self._semaphore.acquire())
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait(
            {acquire_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancel_task in done and cancel_event.is_set():
            if acquire_task in done and acquire_task.result():
                self._semaphore.release()
            raise asyncio.CancelledError
        await acquire_task


async def _close_quietly(resource: Any) -> None:
    try:
        await resource.close()
    except Exception:
        pass
