"""Guest-first probing and user-authorized interactive login orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from threading import Event
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import AUTH_POLICIES, auth_policy_for_key
from src.auth.state_filter import filter_state_for_policy
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.crawler.navigation import stabilize_rendered_page
from src.screenshot.browser import browser_context_options, browser_launch_options
from src.tools.page_access import inspect_http_response, inspect_page_access


ProgressCallback = Callable[[str, AuthStatus, str], None]
_STEALTH_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "libs" / "stealth.min.js"


class AuthManagerService:
    def __init__(
        self,
        config: TaskConfig,
        store: AuthProfileStore,
        *,
        legacy_state_path: Path | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._legacy_state_path = (
            Path(legacy_state_path).expanduser().resolve()
            if legacy_state_path is not None
            else None
        )

    async def probe_all_guest(
        self,
        *,
        cancel_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[AuthProbeResult]:
        results: list[AuthProbeResult] = []
        playwright = None
        browser = None
        guest_config = replace(
            self._config,
            headless=True,
            max_concurrency=1,
        )
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                **browser_launch_options(guest_config)
            )
            for policy in AUTH_POLICIES:
                if cancel_event is not None and cancel_event.is_set():
                    break
                _publish(
                    on_progress,
                    policy.platform_key,
                    AuthStatus.PROBING,
                    "正在以游客模式验证真实内容页。",
                )
                try:
                    result = await self._probe_guest_in_browser(
                        browser,
                        guest_config,
                        policy.platform_key,
                        cancel_event,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    result = _result(
                        policy.platform_key,
                        AuthStatus.ERROR,
                        policy.probe_url,
                        None,
                        f"游客验证失败：{type(error).__name__}",
                    )
                self._store.record_result(result)
                _publish(
                    on_progress,
                    policy.platform_key,
                    result.status,
                    result.message,
                )
                results.append(result)
        finally:
            if browser is not None:
                await _close_quietly(browser)
            if playwright is not None:
                await playwright.stop()
        return results

    async def _probe_guest_in_browser(
        self,
        browser: Any,
        config: TaskConfig,
        platform_key: str,
        cancel_event: Event | None,
    ) -> AuthProbeResult:
        policy = auth_policy_for_key(platform_key)
        context = await browser.new_context(
            **browser_context_options(config)
        )
        try:
            if config.enable_stealth and _STEALTH_SCRIPT_PATH.is_file():
                await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))
            page = await context.new_page()
            response = await _navigate(page, policy.probe_url, config)
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            barrier = inspect_http_response(
                int(response.status) if response is not None else None
            )
            if barrier is None:
                barrier = await inspect_page_access(
                    page,
                    str(page.url),
                    policy.probe_url,
                )
            if barrier is not None:
                return _barrier_result(
                    platform_key,
                    policy.probe_url,
                    str(page.url),
                    barrier,
                    False,
                )
            return _result(
                platform_key,
                AuthStatus.GUEST_OK,
                policy.probe_url,
                str(page.url),
                "游客模式可访问，无需保存登录态。",
            )
        finally:
            await _close_quietly(context)

    async def probe(
        self,
        platform_key: str,
        *,
        use_saved_state: bool = True,
        interactive: bool = False,
        phone: str | None = None,
        cancel_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> AuthProbeResult:
        policy = auth_policy_for_key(platform_key)
        saved_state, state_source = self._initial_state(platform_key, use_saved_state)
        used_saved_state = saved_state is not None
        browser = None
        context = None
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            try:
                auth_config = replace(
                    self._config,
                    headless=not interactive,
                    max_concurrency=1,
                )
                browser = await playwright.chromium.launch(
                    **browser_launch_options(auth_config)
                )
                context = await browser.new_context(
                    **browser_context_options(auth_config, saved_state)
                )
                if auth_config.enable_stealth and _STEALTH_SCRIPT_PATH.is_file():
                    await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))
                page = await context.new_page()
                response = await _navigate(page, policy.probe_url, auth_config)
                barrier = inspect_http_response(
                    int(response.status) if response is not None else None
                )
                if barrier is None:
                    barrier = await inspect_page_access(
                        page,
                        str(page.url),
                        policy.probe_url,
                    )

                if barrier is not None and barrier.manual_recoverable and interactive:
                    _publish(
                        on_progress,
                        platform_key,
                        AuthStatus.WAITING_USER,
                        "请在平台页面中完成人工登录、扫码或验证码。",
                    )
                    if phone and policy.phone_assist:
                        await _fill_phone_without_submitting(page, phone)
                    barrier = await _wait_for_user(
                        page,
                        policy.probe_url,
                        self._config.manual_intervention_timeout_seconds,
                        cancel_event,
                    )
                    if barrier is not None and barrier.code == "CONTENT_REDIRECTED_TO_HOME":
                        await _navigate(page, policy.probe_url, auth_config)
                        barrier = await inspect_page_access(
                            page,
                            str(page.url),
                            policy.probe_url,
                        )

                if barrier is not None:
                    result = _barrier_result(
                        platform_key,
                        policy.probe_url,
                        str(page.url),
                        barrier,
                        used_saved_state,
                    )
                    self._store.record_result(result)
                    _publish(on_progress, platform_key, result.status, result.message)
                    return result

                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError
                if not used_saved_state and not interactive:
                    result = _result(
                        platform_key,
                        AuthStatus.GUEST_OK,
                        policy.probe_url,
                        str(page.url),
                        "游客模式可访问，无需保存登录态。",
                    )
                    self._store.record_result(result)
                    _publish(on_progress, platform_key, result.status, result.message)
                    return result

                candidate = await context.storage_state(indexed_db=True)
                if state_source == "legacy":
                    candidate = filter_state_for_policy(candidate, platform_key)
                _publish(
                    on_progress,
                    platform_key,
                    AuthStatus.VALIDATING,
                    "正在使用新 context 复验保存后的登录态。",
                )
                validation = await self._validate_candidate(
                    browser,
                    auth_config,
                    platform_key,
                    candidate,
                    cancel_event,
                )
                if validation.status == AuthStatus.VALID:
                    self._store.commit_validated_state(
                        platform_key,
                        candidate,
                        validation,
                        phone=phone,
                    )
                else:
                    self._store.record_result(validation)
                _publish(
                    on_progress,
                    platform_key,
                    validation.status,
                    validation.message,
                )
                return validation
            finally:
                if context is not None:
                    await _close_quietly(context)
                if browser is not None:
                    await _close_quietly(browser)
                await playwright.stop()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result = _result(
                platform_key,
                AuthStatus.ERROR,
                policy.probe_url,
                None,
                f"登录态验证失败：{type(error).__name__}",
            )
            self._store.record_result(result)
            _publish(on_progress, platform_key, result.status, result.message)
            return result

    async def _validate_candidate(
        self,
        browser: Any,
        config: TaskConfig,
        platform_key: str,
        state: dict[str, Any],
        cancel_event: Event | None,
    ) -> AuthProbeResult:
        policy = auth_policy_for_key(platform_key)
        validation_context = await browser.new_context(
            **browser_context_options(config, state)
        )
        try:
            page = await validation_context.new_page()
            response = await _navigate(page, policy.probe_url, config)
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            barrier = inspect_http_response(
                int(response.status) if response is not None else None
            )
            if barrier is None:
                barrier = await inspect_page_access(
                    page,
                    str(page.url),
                    policy.probe_url,
                )
            if barrier is not None:
                return _barrier_result(
                    platform_key,
                    policy.probe_url,
                    str(page.url),
                    barrier,
                    True,
                )
            return _result(
                platform_key,
                AuthStatus.VALID,
                policy.probe_url,
                str(page.url),
                "新 context 复验成功，登录态已安全保存。",
                used_saved_state=True,
            )
        finally:
            await _close_quietly(validation_context)

    def _initial_state(
        self,
        platform_key: str,
        enabled: bool,
    ) -> tuple[dict[str, Any] | None, str]:
        if not enabled:
            return None, "guest"
        stored = self._store.load_state(platform_key)
        if stored is not None:
            return stored, "profile"
        if self._legacy_state_path is None or not self._legacy_state_path.is_file():
            return None, "guest"
        try:
            value = json.loads(self._legacy_state_path.read_text(encoding="utf-8"))
        except Exception:
            return None, "guest"
        return (value, "legacy") if isinstance(value, dict) else (None, "guest")


async def _navigate(page: Any, url: str, config: TaskConfig) -> Any | None:
    response = None
    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=config.page_timeout_seconds * 1_000,
        )
    except Exception:
        # A partially rendered page may still provide a precise access barrier.
        pass
    await stabilize_rendered_page(page, config.page_stabilize_milliseconds)
    return response


async def _wait_for_user(
    page: Any,
    original_url: str,
    timeout_seconds: int,
    cancel_event: Event | None,
) -> Any | None:
    remaining = max(0, timeout_seconds)
    barrier = await inspect_page_access(page, str(page.url), original_url)
    while barrier is not None and barrier.manual_recoverable and remaining > 0:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        await page.wait_for_timeout(1_000)
        remaining -= 1
        barrier = await inspect_page_access(page, str(page.url), original_url)
    return barrier


async def _fill_phone_without_submitting(page: Any, phone: str) -> bool:
    selectors = (
        "input[type='tel']",
        "input[name*='phone' i]",
        "input[placeholder*='手机号']",
        "input[placeholder*='手机号码']",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=500):
                await locator.fill(phone)
                return True
        except Exception:
            continue
    return False


def _barrier_result(
    platform_key: str,
    original_url: str,
    final_url: str,
    barrier: Any,
    used_saved_state: bool,
) -> AuthProbeResult:
    if barrier.code == "LOGIN_REQUIRED":
        status = AuthStatus.EXPIRED if used_saved_state else AuthStatus.AUTH_REQUIRED
    elif barrier.manual_recoverable:
        status = AuthStatus.CHALLENGE
    elif barrier.code in {
        "CONTENT_REDIRECTED_TO_HOME",
        "CONTENT_NOT_FOUND",
        "CONTENT_UNAVAILABLE",
        "EMPTY_RENDERED_PAGE",
    }:
        status = AuthStatus.INVALID_URL
    elif barrier.code == "HTTP_401":
        status = AuthStatus.EXPIRED if used_saved_state else AuthStatus.AUTH_REQUIRED
    elif barrier.code in {
        "HTTP_403",
        "HTTP_405_ACCESS_RESTRICTED",
        "HTTP_429",
        "ACCESS_CHALLENGE",
    }:
        status = AuthStatus.ACCESS_BLOCKED
    else:
        status = AuthStatus.ERROR
    return AuthProbeResult(
        platform_key=platform_key,
        status=status,
        checked_at=datetime.now().astimezone(),
        original_url=original_url,
        final_url=final_url,
        barrier_code=barrier.code,
        message=barrier.message,
        used_saved_state=used_saved_state,
    )


def _result(
    platform_key: str,
    status: AuthStatus,
    original_url: str,
    final_url: str | None,
    message: str,
    *,
    used_saved_state: bool = False,
) -> AuthProbeResult:
    return AuthProbeResult(
        platform_key=platform_key,
        status=status,
        checked_at=datetime.now().astimezone(),
        original_url=original_url,
        final_url=final_url,
        message=message,
        used_saved_state=used_saved_state,
    )


def _publish(
    callback: ProgressCallback | None,
    platform_key: str,
    status: AuthStatus,
    message: str,
) -> None:
    if callback is not None:
        callback(platform_key, status, message)


async def _close_quietly(resource: Any) -> None:
    try:
        await resource.close()
    except Exception:
        pass
