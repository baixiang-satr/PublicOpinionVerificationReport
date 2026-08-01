"""Re-validate a platform's preserved login state inside the crawl browser.

A profile marked EXPIRED keeps its encrypted state file.  Before a crawl
pauses the whole platform on that marker, this module gives the preserved
cookies one fair chance against the platform probe candidates: a working
page restores the profile to VALID (self-heal), and only an explicit login
wall confirms the expiry.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.login_evidence import (
    page_authenticated_ui_state,
    state_has_authenticated_session,
)
from src.auth.registry import auth_policy_for_key
from src.auth.store import AuthProfileStore, AuthStateStoreError
from src.config.settings import TaskConfig
from src.crawler.navigation import stabilize_rendered_page
from src.screenshot.browser_options import browser_context_options
from src.screenshot.browser_runtime import close_quietly
from src.tools.page_access import inspect_http_response, inspect_page_access


logger = logging.getLogger(__name__)

_AUTH_WALL_CODES = {"LOGIN_REQUIRED", "HTTP_401"}


async def revalidate_platform_profile(
    browser: Any,
    config: TaskConfig,
    store: AuthProfileStore,
    platform_key: str,
    *,
    stealth_script_path: str | None = None,
) -> bool:
    """Probe the platform with the preserved state; restore VALID on success.

    Returns ``True`` when any probe candidate renders without a barrier (the
    profile is committed back as VALID).  Returns ``False`` when no candidate
    renders; only an explicit login wall also records EXPIRED, since dead or
    risk-blocked probe pages cannot prove the cookies died.
    """

    try:
        policy = auth_policy_for_key(platform_key)
    except KeyError:
        return False
    try:
        state = store.load_state(platform_key, include_inactive=True)
    except AuthStateStoreError:
        return False
    if state is None:
        return False
    if state_has_authenticated_session(platform_key, state) is False:
        store.record_result(
            AuthProbeResult(
                platform_key=platform_key,
                status=AuthStatus.EXPIRED,
                checked_at=datetime.now().astimezone(),
                original_url=policy.probe_url,
                barrier_code="LOGIN_EVIDENCE_MISSING",
                message=(
                    "保存状态只有游客/设备 Cookie，没有账号级登录凭据；"
                    "请在登录态管理中重新登录。"
                ),
                used_saved_state=True,
            )
        )
        return False

    saw_login_wall = False
    for probe_url in policy.probe_candidates:
        context = None
        try:
            context = await browser.new_context(
                **browser_context_options(config, state)
            )
            if stealth_script_path:
                await context.add_init_script(path=stealth_script_path)
            page = await context.new_page()
            response = await _navigate(page, probe_url, config)
            barrier = inspect_http_response(
                int(response.status) if response is not None else None
            )
            if barrier is None:
                barrier = await inspect_page_access(page, str(page.url), probe_url)
            if barrier is None:
                if await page_authenticated_ui_state(page) is False:
                    saw_login_wall = True
                    continue
                refreshed = await context.storage_state(indexed_db=True)
                store.commit_validated_state(
                    platform_key,
                    refreshed,
                    AuthProbeResult(
                        platform_key=platform_key,
                        status=AuthStatus.VALID,
                        checked_at=datetime.now().astimezone(),
                        original_url=probe_url,
                        final_url=str(page.url),
                        message="抓取前自动复验成功，登录态已恢复有效。",
                        used_saved_state=True,
                    ),
                )
                logger.info(
                    "Auth profile for %s self-healed via probe %s",
                    platform_key,
                    probe_url,
                )
                return True
            if barrier.code in _AUTH_WALL_CODES:
                saw_login_wall = True
        except Exception as error:
            logger.warning(
                "Auth revalidation probe failed for %s: %s",
                platform_key,
                error,
            )
        finally:
            if context is not None:
                await close_quietly(context, timeout=2.0)

    if saw_login_wall:
        store.record_result(
            AuthProbeResult(
                platform_key=platform_key,
                status=AuthStatus.EXPIRED,
                checked_at=datetime.now().astimezone(),
                original_url=policy.probe_url,
                barrier_code="LOGIN_REQUIRED",
                message="抓取前自动复验确认登录态已过期；请在登录态管理中重新登录。",
                used_saved_state=True,
            )
        )
    return False


async def _navigate(page: Any, url: str, config: TaskConfig) -> Any | None:
    response = None
    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=config.page_timeout_seconds * 1_000,
        )
    except Exception:
        # A partially rendered page may still provide a precise barrier.
        pass
    await stabilize_rendered_page(page, config.page_stabilize_milliseconds)
    return response
