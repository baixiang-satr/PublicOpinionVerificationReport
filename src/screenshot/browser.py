"""Owned Playwright Chromium lifecycle with anti-detection and stealth capabilities."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import auth_policy_for_key, auth_policy_for_url
from src.auth.state_filter import filter_state_for_policy
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
from src.screenshot.stealth import apply_extra_stealth

logger = logging.getLogger(__name__)

__all__ = [
    "BrowserPool",
    "BrowserUnavailableError",
    "browser_context_options",
    "browser_launch_options",
]


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
                await asyncio.wait_for(
                    self._save_login_states(slots),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(
                    "Timed out refreshing authentication profiles during shutdown; "
                    "continuing browser cleanup."
                )
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
        if (
            url
            and policy is not None
            and _prefer_guest_for_public_share(url, policy.platform_key)
        ):
            # Public XHS shares remain usable as isolated guests only when no
            # verified platform state exists. A verified login must win:
            # author-page navigation and later screenshots need that session.
            return (
                f"guest:{policy.platform_key}",
                policy.platform_key,
                "guest",
                None,
            )
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
                    slot.context.storage_state(indexed_db=True),
                    timeout=2.0,
                )
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


def _prefer_guest_for_public_share(url: str, platform_key: str) -> bool:
    if platform_key != "xiaohongshu":
        return False
    parts = urlsplit(url)
    if not (
        parts.path.startswith("/explore/")
        or parts.path.startswith("/discovery/item/")
    ):
        return False
    query = parse_qs(parts.query, keep_blank_values=True)
    return (
        "app_share" in query.get("xsec_source", ())
        or "share_channel" in query
        or "xhsshare" in query
    )
