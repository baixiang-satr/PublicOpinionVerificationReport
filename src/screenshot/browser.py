"""Owned Playwright Chromium lifecycle with anti-detection and stealth capabilities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.login_evidence import state_has_authenticated_session
from src.auth.registry import auth_policy_for_key, auth_policy_for_url
from src.auth.store import AuthProfileStore, AuthStateStoreError
from src.config.settings import TaskConfig
from src.screenshot.browser_options import (
    STEALTH_SCRIPT_PATH as _STEALTH_SCRIPT_PATH,
)
from src.screenshot.browser_options import (
    browser_context_options,
    browser_launch_options,
    launch_headed_with_fallback,
)
from src.screenshot.browser_runtime import (
    close_quietly,
    connection_closed,
)
from src.screenshot.browser_state import preserve_indexed_db
from src.screenshot.stealth import apply_extra_stealth

logger = logging.getLogger(__name__)

__all__ = [
    "AuthenticationRequiredError",
    "BrowserPool",
    "BrowserUnavailableError",
    "browser_context_options",
    "browser_launch_options",
]


class BrowserUnavailableError(RuntimeError):
    """Raised when Playwright or its owned Chromium executable is unavailable."""


class AuthenticationRequiredError(RuntimeError):
    """Raised before navigation when a platform has no validated state."""


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
        # All evidence crawlers are intentionally headed.  Enforce this at
        # the browser boundary as well as in the UI/config defaults so direct
        # library callers cannot accidentally launch an unauthenticated
        # background crawler.
        self._config = replace(config, headless=False)
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
                if self._config.headless:
                    self._browser = await self._playwright.chromium.launch(**launch_options)
                else:
                    # Interactive windows get real codecs + fingerprint via
                    # the Edge→Chrome→Chromium channel fallback chain.
                    self._browser = await launch_headed_with_fallback(
                        self._playwright,
                        self._config,
                        launch_options,
                    )

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

    async def close(self, *, persist_login_state: bool = True) -> None:
        slots = tuple(self._contexts.values())
        self._contexts.clear()
        self._context_ids.clear()
        if slots and persist_login_state and self._driver_is_connected():
            try:
                # Do not cancel the whole refresh midway: Playwright may still
                # be collecting IndexedDB on an internal page, and tearing
                # the browser down at that instant leaks TargetClosed future
                # warnings to the console.
                await self._save_login_states(slots)
            except Exception as error:
                logger.warning(
                    "Unable to refresh authentication profiles during shutdown: %s",
                    error,
                )
        if slots:
            await asyncio.gather(
                *(close_quietly(slot.context, timeout=2.0) for slot in slots),
                return_exceptions=True,
            )
        if self._browser is not None:
            try:
                await close_quietly(self._browser, timeout=3.0)
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=3.0)
            except Exception:
                pass
            finally:
                self._playwright = None

    async def close_for_cancellation(self) -> None:
        """Fast shutdown that never persists state from a cancelled task."""

        await self.close(persist_login_state=False)

    def _driver_is_connected(self) -> bool:
        if self._browser is None:
            return False
        checker = getattr(self._browser, "is_connected", None)
        if not callable(checker):
            return True
        try:
            return bool(checker())
        except Exception:
            return False

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
                await close_quietly(page)
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

            context_options = browser_context_options(
                self._config,
                storage_state,
                platform_key=platform_key,
            )

            try:
                context = await self._browser.new_context(**context_options)
            except Exception as error:
                if "storage_state" not in context_options:
                    raise
                if platform_key is not None:
                    if self._auth_store is not None:
                        self._auth_store.record_result(
                            AuthProbeResult(
                                platform_key=platform_key,
                                status=AuthStatus.ERROR,
                                checked_at=datetime.now().astimezone(),
                                original_url=url or "",
                                barrier_code="AUTH_CONTEXT_RESTORE_FAILED",
                                message=(
                                    "浏览器无法载入已验证登录态；已拒绝回退到游客会话，"
                                    "请重新登录后再抓取。"
                                ),
                                used_saved_state=True,
                            )
                        )
                    raise AuthenticationRequiredError(
                        "浏览器无法载入该平台的已验证登录态；"
                        "为避免生成未登录截图，任务已在访问前停止。"
                    ) from error
                logger.warning(
                    "Legacy browser state is unreadable; starting a fresh guest session: %s",
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

    async def revalidate_platform_profile(self, platform_key: str) -> bool:
        """Give a non-VALID profile's preserved state one fair probe.

        Used by the crawl engine before pausing a whole platform: preserved
        cookies that still work restore the profile to VALID (self-heal), so
        a stale EXPIRED marker never blocks a batch right after the user
        logged in.
        """

        if self._auth_store is None or self._browser is None:
            return False
        from src.screenshot.auth_revalidation import revalidate_platform_profile

        return await revalidate_platform_profile(
            self._browser,
            self._config,
            self._auth_store,
            platform_key,
            stealth_script_path=(
                str(_STEALTH_SCRIPT_PATH) if self._stealth_loaded else None
            ),
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
        if slot is None or slot.platform_key is None:
            return
        if barrier_code not in {"LOGIN_REQUIRED", "HTTP_401"}:
            # A dead URL, empty page, HTTP 403, captcha, API response or
            # parser problem is page-specific risk control; it does not
            # prove anything about the saved login state.
            return
        # A single content URL's login wall must not downgrade the persisted
        # authentication profile: the barrier may be URL-specific, and
        # wiping the profile forced users to log in again after every
        # transient failure.  Only the auth manager's fresh-context probe
        # against the platform probe URL may mark a profile EXPIRED.  Here we
        # just evict the in-memory context so later URLs do not reuse the
        # session that hit the wall; the per-record error still reports the
        # barrier for the quality report.
        logger.warning(
            "Login barrier %s on %s for platform %s; kept saved login state, "
            "re-validate from the auth manager if it persists.",
            barrier_code,
            original_url,
            slot.platform_key,
        )
        self._invalidate_auth_context(slot)

    def _invalidate_auth_context(self, slot: _ContextSlot) -> None:
        """Evict a profile-backed browser context after its saved state proves invalid."""
        self._contexts.pop(slot.key, None)
        self._context_ids.pop(id(slot.context), None)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(close_quietly(slot.context, timeout=2.0))

    def _context_spec(
        self,
        url: str | None,
    ) -> tuple[str, str | None, str, Any | None]:
        policy = auth_policy_for_url(url or "") if url else None
        if policy is not None:
            if self._auth_store is None:
                raise AuthenticationRequiredError(
                    f"{policy.display_name} 未配置登录态存储；"
                    "请先在“管理平台登录态”中完成登录。"
                )
            try:
                state = self._auth_store.load_state(policy.platform_key)
            except AuthStateStoreError:
                logger.warning(
                    "Encrypted authentication state is unreadable for %s; "
                    "refusing guest fallback without deleting it.",
                    policy.platform_key,
                )
                self._auth_store.record_result(
                    AuthProbeResult(
                        platform_key=policy.platform_key,
                        status=AuthStatus.ERROR,
                        checked_at=datetime.now().astimezone(),
                        original_url=url or policy.probe_url,
                        barrier_code="AUTH_STATE_UNREADABLE",
                        message="本机加密登录态无法读取；已拒绝回退到游客会话。",
                        used_saved_state=True,
                    )
                )
                state = None
            if state is not None:
                if state_has_authenticated_session(policy.platform_key, state) is False:
                    raise AuthenticationRequiredError(
                        f"{policy.display_name} 保存的是游客会话，未检测到账号级登录凭据；"
                        "请先在“管理平台登录态”中执行“登录 / 更新”。"
                    )
                return (
                    f"profile:{policy.platform_key}",
                    policy.platform_key,
                    "profile",
                    state,
                )
            if policy.requires_valid_state:
                raise AuthenticationRequiredError(
                    f"{policy.display_name} 缺少已验证登录态；"
                    "请先在“管理平台登录态”中完成登录。"
                )
        legacy_path = self._storage_state_path()
        if legacy_path is not None and legacy_path.is_file():
            if self._auth_store is not None:
                # Per-platform profiles are mandatory. The legacy file is
                # consumed only by AuthManagerService as a migration source.
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
            if not self._driver_is_connected():
                break
            try:
                state = await asyncio.wait_for(
                    # Crawling never mutates IndexedDB intentionally.  Asking
                    # Playwright to re-read it may create hidden navigation
                    # tasks during shutdown, so refresh cookies/localStorage
                    # and preserve the already validated IndexedDB payload.
                    slot.context.storage_state(),
                    timeout=2.0,
                )
                previous = self._auth_store.load_state(
                    slot.platform_key,
                    include_inactive=True,
                )
                state = preserve_indexed_db(previous, state)
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
                if connection_closed(error):
                    break

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
