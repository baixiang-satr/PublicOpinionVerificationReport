"""Mandatory saved-state validation and user-authorized login orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from src.auth.batch import (
    login_all_missing as run_login_all_missing,
)
from src.auth.batch import (
    probe_all_saved as run_probe_all_saved,
)
from src.auth.login_evidence import wait_for_login_evidence
from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.probe_helpers import (
    ProgressCallback,
    _activate_login_trigger,
    _barrier_result,
    _close_quietly,
    _fill_phone_without_submitting,
    _navigate_login,
    _navigate_probe_candidates,
    _publish,
    _result,
)
from src.auth.registry import AUTH_POLICIES, auth_policy_for_key
from src.auth.state_filter import filter_state_for_policy
from src.auth.store import AuthProfileStore
from src.auth.validation import validate_candidate
from src.auth.window_visibility import reveal_window_once, stage_window_offscreen
from src.config.settings import TaskConfig
from src.screenshot.browser_options import (
    browser_context_options,
    browser_launch_options,
)

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

    async def probe_all_saved(
        self,
        *,
        cancel_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[AuthProbeResult]:
        """Validate every platform's saved state without opening login UI."""

        return await run_probe_all_saved(
            self,
            AUTH_POLICIES,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )

    async def login_all_missing(
        self,
        *,
        cancel_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[AuthProbeResult]:
        """Interactively establish every missing platform login once.

        Existing VALID profiles are preserved and skipped.  Missing, expired,
        or unreadable profiles are handled one platform at a time so cookies
        and local storage remain isolated by authentication scope.
        """

        return await run_login_all_missing(
            self,
            AUTH_POLICIES,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )

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
        login_confirmation_event: Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> AuthProbeResult:
        policy = auth_policy_for_key(platform_key)
        saved_state, state_source = self._initial_state(platform_key, use_saved_state)
        had_valid_state = self._store.has_valid_state(platform_key)
        if interactive:
            # "登录 / 更新" is an explicit request for a fresh account login.
            # Never preload the old profile into this window: otherwise a
            # signed-in landing page can be mistaken for newly completed login
            # and the browser closes after only a brief flash.  The old state
            # remains encrypted on disk until a new candidate succeeds.
            saved_state = None
            state_source = "guest"
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
                    background_crawl_browser=False,
                    max_concurrency=1,
                )
                launch_options = browser_launch_options(auth_config)
                if interactive:
                    # Chromium otherwise paints a visible about:blank window,
                    # navigates, and resizes again.  Stage that first paint
                    # outside the desktop and reveal the completed login page
                    # through CDP exactly once.
                    launch_options = stage_window_offscreen(
                        launch_options,
                        width=auth_config.viewport_width,
                        height=auth_config.viewport_height,
                    )
                # One login click creates exactly one browser process.  Auth
                # UI does not cycle through Edge/Chrome/Chromium fallbacks,
                # which previously caused a series of one-second windows.
                browser = await playwright.chromium.launch(**launch_options)
                context = await browser.new_context(
                    **browser_context_options(auth_config, saved_state)
                )
                if auth_config.enable_stealth and _STEALTH_SCRIPT_PATH.is_file():
                    await context.add_init_script(path=str(_STEALTH_SCRIPT_PATH))
                page = await context.new_page()
                if interactive:
                    # A login action opens exactly one page for exactly the
                    # selected platform.  Content probe URLs are reserved for
                    # the later, hidden fresh-context validation.
                    await _navigate_login(page, policy.login_url, auth_config)
                    if policy.open_login_trigger:
                        for _attempt in range(12):
                            if await _activate_login_trigger(page):
                                break
                            await page.wait_for_timeout(500)
                    revealed = await reveal_window_once(
                        page,
                        width=auth_config.viewport_width,
                        height=auth_config.viewport_height,
                    )
                    if not revealed and hasattr(context, "new_cdp_session"):
                        raise RuntimeError("无法稳定显示登录窗口，请重试。")
                    baseline_state = await context.storage_state(indexed_db=True)
                    _publish(
                        on_progress,
                        platform_key,
                        AuthStatus.WAITING_USER,
                        (
                            f"已打开{policy.display_name}登录界面；"
                            "请完成登录、扫码或验证码，再回到此窗口点击“完成登录并保存”。"
                        ),
                    )
                    if phone and policy.phone_assist:
                        await _fill_phone_without_submitting(page, phone)
                    authenticated = await wait_for_login_evidence(
                        context,
                        page,
                        platform_key,
                        self._config.manual_intervention_timeout_seconds,
                        cancel_event,
                        baseline_state=baseline_state,
                        confirmation_event=login_confirmation_event,
                    )
                    if not authenticated:
                        if had_valid_state:
                            result = _result(
                                platform_key,
                                AuthStatus.VALID,
                                policy.login_url,
                                str(page.url),
                                "本次重新登录未完成，原有有效登录态已保留。",
                                used_saved_state=True,
                            )
                            self._store.record_result(result)
                            _publish(
                                on_progress,
                                platform_key,
                                result.status,
                                result.message,
                            )
                            return result
                        result = AuthProbeResult(
                            platform_key=platform_key,
                            status=AuthStatus.AUTH_REQUIRED,
                            checked_at=datetime.now().astimezone(),
                            original_url=policy.login_url,
                            final_url=str(page.url),
                            barrier_code="LOGIN_EVIDENCE_MISSING",
                            message=(
                                "本次未检测到登录成功，未覆盖已保存的登录态；"
                                "请重新点击该平台的“登录 / 更新”并完成登录。"
                            ),
                            used_saved_state=used_saved_state,
                        )
                        self._store.record_result(result)
                        _publish(
                            on_progress,
                            platform_key,
                            result.status,
                            result.message,
                        )
                        return result
                else:
                    barrier, _probe_url = await _navigate_probe_candidates(
                        page,
                        policy,
                        auth_config,
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
                        _publish(
                            on_progress,
                            platform_key,
                            result.status,
                            result.message,
                        )
                        return result

                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError
                if not used_saved_state and not interactive:
                    if policy.requires_valid_state:
                        result = AuthProbeResult(
                            platform_key=platform_key,
                            status=AuthStatus.AUTH_REQUIRED,
                            checked_at=datetime.now().astimezone(),
                            original_url=policy.probe_url,
                            final_url=str(page.url),
                            barrier_code="LOGIN_STATE_MISSING",
                            message=(
                                "该平台尚未保存已验证登录态；"
                                "请在登录态管理中点击该平台的“登录 / 更新”。"
                            ),
                            used_saved_state=False,
                        )
                        self._store.record_result(result)
                        _publish(
                            on_progress,
                            platform_key,
                            result.status,
                            result.message,
                        )
                        return result
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
                if interactive:
                    # Interactive login must not open any probe/content page.
                    # Clear login evidence is persisted immediately; the
                    # regular crawl preflight performs the fresh-context
                    # validation later in its already-background browser.
                    result = _result(
                        platform_key,
                        AuthStatus.VALID,
                        policy.login_url,
                        str(page.url),
                        (
                            "登录成功，该平台登录态已加密保存；"
                            "后续抓取将自动加载并在后台复验。"
                        ),
                        used_saved_state=True,
                    )
                    self._store.commit_validated_state(
                        platform_key,
                        candidate,
                        result,
                        phone=phone,
                    )
                    _publish(
                        on_progress,
                        platform_key,
                        result.status,
                        result.message,
                    )
                    return result
                _publish(
                    on_progress,
                    platform_key,
                    AuthStatus.VALIDATING,
                    "正在后台复验该平台已保存的登录态。",
                )
                validation = await validate_candidate(
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
