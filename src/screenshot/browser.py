"""Owned Playwright Chromium lifecycle with anti-detection and stealth capabilities."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import random
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from src.config.settings import TaskConfig
from src.utils.user_agent import UserAgentManager


logger = logging.getLogger(__name__)

_STEALTH_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "libs" / "stealth.min.js"

# ── Anti-detection Chromium launch arguments ─────────────────────────────
# Reference: MediaCrawler (https://github.com/NanmiCoder/MediaCrawler)
# These args help hide Playwright automation fingerprints from target sites.
_ANTI_DETECTION_ARGS = (
    # ── Core automation hiding ───────────────────────────────────────
    "--disable-blink-features=AutomationControlled",
    "--exclude-switches=enable-automation",
    "--disable-infobars",
    # ── Sandbox / shared memory ──────────────────────────────────────
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    # ── Background throttling prevention ─────────────────────────────
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--disable-hang-monitor",
    # ── Feature flags ────────────────────────────────────────────────
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-web-security",
    "--disable-sync",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    # ── Performance ──────────────────────────────────────────────────
    "--disable-gpu",
    # ── Misc ─────────────────────────────────────────────────────────
    "--no-first-run",
    "--no-default-browser-check",
    "--hide-scrollbars",
    "--mute-audio",
)


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
        self._user_agent_mgr = UserAgentManager()
        self._stealth_loaded = False

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

                # Build launch args: anti-detection + user extras
                launch_args = list(_ANTI_DETECTION_ARGS)
                if self._config.extra_chromium_args:
                    launch_args.extend(self._config.extra_chromium_args)

                launch_options: dict[str, Any] = {
                    "headless": self._config.headless,
                    "args": launch_args,
                }

                # ── Proxy support (参考 MediaCrawler proxy_ip_pool) ──
                if self._config.proxy_url:
                    proxy_url = self._config.proxy_url
                    launch_options["proxy"] = {"server": proxy_url}
                    logger.info("Browser configured with proxy: %s", _mask_proxy(proxy_url))

                self._browser = await self._playwright.chromium.launch(**launch_options)

                # Pre-load stealth script
                if self._config.enable_stealth and _STEALTH_SCRIPT_PATH.is_file():
                    self._stealth_loaded = True
                    logger.info("Stealth anti-detection script loaded from %s", _STEALTH_SCRIPT_PATH)
                elif self._config.enable_stealth:
                    logger.warning("Stealth script not found at %s — stealth disabled", _STEALTH_SCRIPT_PATH)

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

            # ── Rotate User-Agent per context (参考 MediaCrawler 随机 UA) ──
            user_agent = self._config.user_agent or self._user_agent_mgr.random()

            # ── Randomize viewport slightly to avoid fingerprint lock ──
            base_w, base_h = 1440, 900
            viewport_w = base_w + random.randint(-20, 20)
            viewport_h = base_h + random.randint(-10, 10)

            context_options: dict[str, Any] = {
                "user_agent": user_agent,
                "viewport": {"width": viewport_w, "height": viewport_h},
                "locale": "zh-CN",
                "timezone_id": self._config.timezone,
                # Extra fingerprint randomization
                "device_scale_factor": random.choice([1, 2]),
                "is_mobile": False,
                "has_touch": False,
                "color_scheme": random.choice(["light", "dark"]),
                "reduced_motion": "no-preference",
                "forced_colors": "none",
            }

            # ── Storage state (login cookies) ─────────────────────────
            storage_state = self._config.storage_state_path
            if storage_state is not None:
                storage_path = Path(storage_state).expanduser().resolve()
                if not storage_path.is_file():
                    raise FileNotFoundError(f"Configured storage state does not exist: {storage_path}")
                context_options["storage_state"] = str(storage_path)

            context = await self._browser.new_context(**context_options)
            self._contexts.add(context)

            # ── Inject stealth anti-detection script ──────────────────
            # 参考 MediaCrawler: browser_context.add_init_script(path="libs/stealth.min.js")
            if self._stealth_loaded:
                await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))

            page = await context.new_page()
            page.set_default_navigation_timeout(self._config.page_timeout_seconds * 1000)
            page.set_default_timeout(self._config.page_timeout_seconds * 1000)

            # ── Extra JS overrides to hide automation traces ──────────
            await self._apply_extra_stealth(page)

            yield page
        finally:
            if page is not None:
                await _close_quietly(page)
            if context is not None:
                self._contexts.discard(context)
                await _close_quietly(context)
            self._semaphore.release()

    async def _apply_extra_stealth(self, page: Any) -> None:
        """Apply additional JS patches that supplement stealth.min.js.

        These overrides hide common automation fingerprints that some sites
        check even after stealth.min.js is applied.
        """
        try:
            await page.evaluate("""
                () => {
                    // Override navigator.webdriver (defence in depth)
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                        configurable: true,
                    });

                    // Override chrome.runtime to look like a real browser
                    if (window.chrome) {
                        Object.defineProperty(window.chrome, 'runtime', {
                            get: () => ({
                                connect: () => null,
                                sendMessage: () => null,
                                onMessage: { addListener: () => {} },
                                onConnect: { addListener: () => {} },
                            }),
                            configurable: true,
                        });
                    }

                    // Mask languages & plugins
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['zh-CN', 'zh', 'en'],
                        configurable: true,
                    });

                    // Override permissions (common detection point)
                    const originalQuery = window.navigator.permissions?.query;
                    if (originalQuery) {
                        window.navigator.permissions.query = (desc) => {
                            if (desc.name === 'notifications') {
                                return Promise.resolve({ state: 'denied' });
                            }
                            return originalQuery.call(window.navigator.permissions, desc);
                        };
                    }
                }
            """)
        except Exception:
            # Extra stealth is best-effort; don't fail the page open on it
            pass

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


def _mask_proxy(proxy_url: str) -> str:
    """Mask password in proxy URL for safe logging."""
    parsed = urlparse(proxy_url)
    if parsed.password:
        return proxy_url.replace(parsed.password, "****")
    return proxy_url
