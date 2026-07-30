"""Guest-first probing and user-authorized interactive login orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.probe_helpers import (
    ProgressCallback,
    _barrier_result,
    _close_quietly,
    _fill_phone_without_submitting,
    _navigate,
    _navigate_probe_candidates,
    _publish,
    _result,
    _wait_for_user,
)
from src.auth.registry import AUTH_POLICIES, auth_policy_for_key
from src.auth.state_filter import filter_state_for_policy
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.screenshot.browser_options import (
    browser_context_options,
    browser_launch_options,
    launch_headed_with_fallback,
)
from src.tools.page_access import inspect_http_response, inspect_page_access

__all__ = ["AuthManagerService", "ProgressCallback", "filter_state_for_policy"]


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
            barrier, _probe_url = await _navigate_probe_candidates(
                page,
                policy,
                config,
            )
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
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
                launch_options = browser_launch_options(auth_config)
                if interactive:
                    browser = await launch_headed_with_fallback(
                        playwright,
                        auth_config,
                        launch_options,
                    )
                else:
                    browser = await playwright.chromium.launch(**launch_options)
                context = await browser.new_context(
                    **browser_context_options(auth_config, saved_state)
                )
                if auth_config.enable_stealth and _STEALTH_SCRIPT_PATH.is_file():
                    await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))
                page = await context.new_page()
                barrier, probe_url = await _navigate_probe_candidates(
                    page,
                    policy,
                    auth_config,
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
                        probe_url,
                        self._config.manual_intervention_timeout_seconds,
                        cancel_event,
                    )
                    if barrier is not None and barrier.code == "CONTENT_REDIRECTED_TO_HOME":
                        await _navigate(page, probe_url, auth_config)
                        barrier = await inspect_page_access(
                            page,
                            str(page.url),
                            probe_url,
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
                if (
                    interactive
                    and validation.status
                    not in {AuthStatus.VALID, AuthStatus.EXPIRED, AuthStatus.AUTH_REQUIRED}
                ):
                    # The user just completed a manual login in the visible
                    # context.  A dead, emptied or risk-blocked probe page
                    # cannot disprove those fresh cookies, so the candidate
                    # state is committed with a caveat instead of discarding
                    # the login work the user just did.
                    validation = _result(
                        platform_key,
                        AuthStatus.VALID,
                        policy.probe_url,
                        validation.final_url,
                        (
                            "人工登录已完成；探测页暂时无法复验"
                            f"（{validation.barrier_code or validation.status.value}），"
                            "已直接保存登录态。"
                        ),
                        used_saved_state=True,
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
        last_inconclusive: AuthProbeResult | None = None
        for probe_url in policy.probe_candidates:
            validation_context = await browser.new_context(
                **browser_context_options(config, state)
            )
            try:
                page = await validation_context.new_page()
                response = await _navigate(page, probe_url, config)
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError
                barrier = inspect_http_response(
                    int(response.status) if response is not None else None
                )
                if barrier is None:
                    barrier = await inspect_page_access(
                        page,
                        str(page.url),
                        probe_url,
                    )
                if barrier is None:
                    return _result(
                        platform_key,
                        AuthStatus.VALID,
                        probe_url,
                        str(page.url),
                        "新 context 复验成功，登录态已安全保存。",
                        used_saved_state=True,
                    )
                result = _barrier_result(
                    platform_key,
                    probe_url,
                    str(page.url),
                    barrier,
                    True,
                )
                if result.status in {AuthStatus.EXPIRED, AuthStatus.AUTH_REQUIRED}:
                    # An explicit login wall is the only definitive proof that
                    # the saved state expired; dead or risk-blocked probe
                    # pages are inconclusive, so try the next candidate.
                    return result
                last_inconclusive = result
            finally:
                await _close_quietly(validation_context)
        return last_inconclusive or _result(
            platform_key,
            AuthStatus.ERROR,
            policy.probe_url,
            None,
            "登录态复验失败：没有可用的探测页。",
            used_saved_state=True,
        )

    def _initial_state(
        self,
        platform_key: str,
        enabled: bool,
    ) -> tuple[dict[str, Any] | None, str]:
        if not enabled:
            return None, "guest"
        # include_inactive: an EXPIRED/CHALLENGE profile's preserved file may
        # still hold working cookies (expiry can be a false positive from a
        # dead probe page).  Loading it here lets a plain re-validation
        # restore VALID without forcing the user through a fresh login.
        stored = self._store.load_state(platform_key, include_inactive=True)
        if stored is not None:
            return stored, "profile"
        if self._legacy_state_path is None or not self._legacy_state_path.is_file():
            return None, "guest"
        try:
            value = json.loads(self._legacy_state_path.read_text(encoding="utf-8"))
        except Exception:
            return None, "guest"
        return (value, "legacy") if isinstance(value, dict) else (None, "guest")
