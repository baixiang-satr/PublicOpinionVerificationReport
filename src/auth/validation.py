"""Fresh-context validation for one candidate authentication state."""

from __future__ import annotations

import asyncio
from datetime import datetime
from threading import Event
from typing import Any

from src.auth.login_evidence import (
    page_authenticated_ui_state,
    state_has_authenticated_session,
)
from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.probe_helpers import _barrier_result, _close_quietly, _navigate, _result
from src.auth.registry import auth_policy_for_key
from src.config.settings import TaskConfig
from src.screenshot.browser_options import browser_context_options
from src.tools.page_access import inspect_http_response, inspect_page_access


async def validate_candidate(
    browser: Any,
    config: TaskConfig,
    platform_key: str,
    state: dict[str, Any],
    cancel_event: Event | None,
) -> AuthProbeResult:
    """Open saved state in a fresh context and prove it is account-scoped."""

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
                barrier = await inspect_page_access(page, str(page.url), probe_url)
            if barrier is None:
                static_evidence = state_has_authenticated_session(platform_key, state)
                ui_state = await page_authenticated_ui_state(page)
                if static_evidence is False:
                    return AuthProbeResult(
                        platform_key=platform_key,
                        status=AuthStatus.AUTH_REQUIRED,
                        checked_at=datetime.now().astimezone(),
                        original_url=probe_url,
                        final_url=str(page.url),
                        barrier_code="LOGIN_EVIDENCE_MISSING",
                        message=(
                            "探测页可以游客访问，但保存状态中没有账号级登录凭据；"
                            "请执行“登录 / 更新”。"
                        ),
                        used_saved_state=True,
                    )
                if ui_state is False:
                    return AuthProbeResult(
                        platform_key=platform_key,
                        status=AuthStatus.EXPIRED,
                        checked_at=datetime.now().astimezone(),
                        original_url=probe_url,
                        final_url=str(page.url),
                        barrier_code="LOGIN_UI_VISIBLE",
                        message=(
                            "探测页仍显示“登录”，保存登录态未在新 context 中生效；"
                            "请执行“登录 / 更新”。"
                        ),
                        used_saved_state=True,
                    )
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
