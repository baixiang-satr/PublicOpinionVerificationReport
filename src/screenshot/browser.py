"""Owned Playwright Chromium lifecycle with anti-detection and stealth capabilities."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import auth_policy_for_key, auth_policy_for_url
from src.auth.state_filter import filter_state_for_policy
from src.auth.store import AuthProfileStore, AuthStateStoreError
from src.config.settings import TaskConfig
from src.screenshot.stealth import apply_extra_stealth


logger = logging.getLogger(__name__)

_STEALTH_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "libs" / "stealth.min.js"

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


def browser_launch_options(config: TaskConfig) -> dict[str, Any]:
    launch_args = [*_ANTI_DETECTION_ARGS, *config.extra_chromium_args]
    options: dict[str, Any] = {
        "headless": config.headless,
        "args": launch_args,
    }
    if config.proxy_url:
        options["proxy"] = {"server": config.proxy_url}
        logger.info("Browser configured with proxy: %s", _mask_proxy(config.proxy_url))
    return options


def browser_context_options(
    config: TaskConfig,
    storage_state: Any | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
        },
        "locale": "zh-CN",
        "timezone_id": config.timezone,
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "color_scheme": "light",
        "reduced_motion": "no-preference",
        "forced_colors": "none",
    }
    if config.user_agent:
        options["user_agent"] = config.user_agent
    if storage_state is not None:
        options["storage_state"] = storage_state
    return options


class BrowserUnavailableError(RuntimeError):
    """Raised when Playwright or its owned Chromium executable is unavailable."""


@dataclass
class _ContextSlot:
    context: Any
    key: str
    platform_key: str | None
    source: str
    validated: bool = False


class BrowserPool:
    def __init__(
        self,
        config: TaskConfig,
        *,
        auth_store: AuthProfileStore | None = None,
    ) -> None:
        self._config = config
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: dict[str, _ContextSlot] = {}
        self._context_ids: dict[int, _ContextSlot] = {}
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._context_lock = asyncio.Lock()
        self._stealth_loaded = False
        self._auth_store = auth_store
        if self._auth_store is None and config.auth_store_dir is not None:
            self._auth_store = AuthProfileStore(config.auth_store_dir)

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

                launch_options = browser_launch_options(self._config)
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
        slots = tuple(self._contexts.values())
        self._contexts.clear()
        self._context_ids.clear()
        if slots:
            await self._save_login_states(slots)
            await asyncio.gather(
                *(_close_quietly(slot.context) for slot in slots),
                return_exceptions=True,
            )
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
    async def page(
        self,
        cancel_event: asyncio.Event | None = None,
        url: str | None = None,
    ) -> AsyncIterator[Any]:
        slot_acquired = False
        page = None
        try:
            await self._acquire_slot(cancel_event)
            slot_acquired = True

            if not self.is_started:
                raise BrowserUnavailableError("BrowserPool.start() must be called before opening a page.")

            context = await self._get_or_create_context(url)
            page = await context.new_page()
            page.set_default_navigation_timeout(self._config.page_timeout_seconds * 1000)
            page.set_default_timeout(self._config.page_timeout_seconds * 1000)

            # ── Extra JS overrides to hide automation traces ──────────
            if self._config.enable_extra_stealth:
                await apply_extra_stealth(page)

            yield page
        finally:
            if page is not None:
                await _close_quietly(page)
            if slot_acquired:
                self._semaphore.release()

    async def _get_or_create_context(self, url: str | None = None) -> Any:
        """Return a guest, legacy, or platform-scoped authenticated context."""

        key, platform_key, source, storage_state = self._context_spec(url)
        existing = self._contexts.get(key)
        if existing is not None:
            return existing.context
        async with self._context_lock:
            existing = self._contexts.get(key)
            if existing is not None:
                return existing.context
            if not self.is_started:
                raise BrowserUnavailableError("BrowserPool.start() must be called before opening a page.")

            context_options = browser_context_options(self._config, storage_state)

            try:
                context = await self._browser.new_context(**context_options)
            except Exception:
                if "storage_state" not in context_options:
                    raise
                logger.warning(
                    "Configured browser login state is unreadable; starting with a fresh session: %s",
                    key,
                )
                context_options.pop("storage_state", None)
                context = await self._browser.new_context(**context_options)
            if self._stealth_loaded:
                await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))
            slot = _ContextSlot(context, key, platform_key, source)
            self._contexts[key] = slot
            self._context_ids[id(context)] = slot
            return context

    def mark_access_valid(self, page: Any, original_url: str) -> None:
        slot = self._context_ids.get(id(page.context))
        if slot is None or slot.source != "profile" or slot.platform_key is None:
            return
        slot.validated = True
        logger.info(
            "Validated reusable authentication profile for %s",
            slot.platform_key,
        )

    def mark_access_invalid(
        self,
        page: Any,
        original_url: str,
        *,
        barrier_code: str,
        message: str,
    ) -> None:
        slot = self._context_ids.get(id(page.context))
        if (
            slot is None
            or slot.source != "profile"
            or slot.platform_key is None
            or self._auth_store is None
        ):
            return
        if barrier_code in {"LOGIN_REQUIRED", "HTTP_401"}:
            status = AuthStatus.EXPIRED
        elif barrier_code in {
            "CAPTCHA_REQUIRED",
            "ACCESS_CHALLENGE",
            "HTTP_403",
        }:
            status = AuthStatus.CHALLENGE
        else:
            # A dead URL, empty page, API response, or parser problem does not
            # prove that an otherwise valid authentication state has expired.
            return
        self._auth_store.record_result(
            AuthProbeResult(
                platform_key=slot.platform_key,
                status=status,
                checked_at=datetime.now().astimezone(),
                original_url=original_url,
                final_url=str(getattr(page, "url", "") or ""),
                barrier_code=barrier_code,
                message=message,
                used_saved_state=True,
            )
        )

    def _context_spec(
        self,
        url: str | None,
    ) -> tuple[str, str | None, str, Any | None]:
        policy = auth_policy_for_url(url or "") if url else None
        if policy is not None and self._auth_store is not None:
            try:
                state = self._auth_store.load_state(policy.platform_key)
            except AuthStateStoreError:
                logger.warning(
                    "Encrypted authentication state is unreadable for %s; "
                    "falling back without exposing or deleting it.",
                    policy.platform_key,
                )
                self._auth_store.record_result(
                    AuthProbeResult(
                        platform_key=policy.platform_key,
                        status=AuthStatus.ERROR,
                        checked_at=datetime.now().astimezone(),
                        original_url=url or policy.probe_url,
                        barrier_code="AUTH_STATE_UNREADABLE",
                        message="本机加密登录态无法读取；本次已回退到游客或旧版兼容会话。",
                        used_saved_state=True,
                    )
                )
                state = None
            if state is not None:
                return (
                    f"profile:{policy.platform_key}",
                    policy.platform_key,
                    "profile",
                    state,
                )
        legacy_path = self._storage_state_path()
        if legacy_path is not None and legacy_path.is_file():
            if self._auth_store is not None:
                if policy is None:
                    return "guest", None, "guest", None
                try:
                    legacy_value = json.loads(
                        legacy_path.read_text(encoding="utf-8")
                    )
                    if isinstance(legacy_value, dict):
                        filtered = filter_state_for_policy(
                            legacy_value,
                            policy.platform_key,
                        )
                        return (
                            f"legacy:{policy.platform_key}",
                            policy.platform_key,
                            "legacy",
                            filtered,
                        )
                except Exception:
                    logger.warning(
                        "Legacy combined authentication state is unreadable; "
                        "using a guest context for %s.",
                        policy.platform_key,
                    )
                return "guest", None, "guest", None
            return "legacy", None, "legacy", str(legacy_path)
        return "guest", None, "guest", None

    async def _save_login_states(self, slots: tuple[_ContextSlot, ...]) -> None:
        if self._auth_store is None:
            legacy = next((slot for slot in slots if slot.key == "legacy"), None)
            if legacy is None and len(slots) == 1:
                legacy = slots[0]
            if legacy is not None:
                await self._save_legacy_login_state(legacy.context)
            return
        for slot in slots:
            if (
                slot.source != "profile"
                or not slot.validated
                or slot.platform_key is None
            ):
                continue
            try:
                state = await slot.context.storage_state(indexed_db=True)
                profile = self._auth_store.profile_for(slot.platform_key)
                validation_url = (
                    profile.validation_url
                    or auth_policy_for_key(slot.platform_key).probe_url
                )
                result = AuthProbeResult(
                    platform_key=slot.platform_key,
                    status=AuthStatus.VALID,
                    checked_at=datetime.now().astimezone(),
                    original_url=validation_url,
                    message="抓取任务复验成功并刷新登录态。",
                    used_saved_state=True,
                )
                self._auth_store.commit_validated_state(
                    slot.platform_key,
                    state,
                    result,
                )
            except Exception as error:
                logger.warning(
                    "Unable to refresh authentication profile for %s: %s",
                    slot.platform_key,
                    error,
                )

    async def _save_legacy_login_state(self, context: Any) -> None:
        """Persist the legacy combined state for backward-compatible callers."""

        storage_path = self._storage_state_path()
        if storage_path is None:
            return
        temporary_path = storage_path.with_suffix(f"{storage_path.suffix}.tmp")
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(temporary_path), indexed_db=True)
            temporary_path.replace(storage_path)
            logger.info("Saved legacy combined browser login state to %s", storage_path)
        except Exception as error:
            temporary_path.unlink(missing_ok=True)
            logger.warning("Unable to save browser login state to %s: %s", storage_path, error)

    def _storage_state_path(self) -> Path | None:
        state = self._config.storage_state_path
        return Path(state).expanduser().resolve() if state is not None else None

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
