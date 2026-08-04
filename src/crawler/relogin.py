"""Interactive re-login hooks for crawl-time authentication failures.

When the crawl proves a platform's saved login state expired (login wall,
HTTP 401, or guest UI with a profile context), these helpers ask the UI
layer to open one interactive login window at a time and let the user
refresh the state, after which the preserved-state heal probe runs again.
All helpers are no-ops when no handler is wired (CLI/tests keep the old
pause behaviour).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.auth.registry import auth_policy_for_key, auth_policy_for_url
from src.crawler.auth_preflight import try_heal_auth
from src.domain.models import RecordResult, UrlTask

# handler(platform_key, display_name, cancel_event) -> True when the user
# completed an interactive login and the new state was persisted.
ReloginHandler = Callable[[str, str, asyncio.Event], Awaitable[bool]]

# Only these mid-crawl failures justify an interactive re-login prompt.
# CAPTCHA / access challenges are not expired logins; dead probe pages say
# nothing about the cookies either.
_LOGIN_FAILURE_CODES = {
    "LOGIN_REQUIRED",
    "HTTP_401",
    "PLATFORM_AUTH_PAUSED",
}


def is_login_failure(result: RecordResult) -> bool:
    return any(error.code in _LOGIN_FAILURE_CODES for error in result.errors)


async def relogin_and_heal(
    handler: ReloginHandler | None,
    browser_pool: Any,
    platform_key: str,
    cancel_event: asyncio.Event,
) -> bool:
    """Prompt for interactive re-login, then re-probe the fresh state."""

    if handler is None or cancel_event.is_set():
        return False
    try:
        display_name = auth_policy_for_key(platform_key).display_name
    except KeyError:
        return False
    if not await handler(platform_key, display_name, cancel_event):
        return False
    return await try_heal_auth(browser_pool, platform_key)


async def heal_or_relogin(
    handler: ReloginHandler | None,
    browser_pool: Any,
    platform_key: str,
    preflight: dict[str, bool],
    cancel_event: asyncio.Event,
) -> bool:
    """Resolve a stored auth block: preflight, hidden heal, then re-login."""

    if preflight.get(platform_key) is True:
        return True
    if platform_key not in preflight and await try_heal_auth(browser_pool, platform_key):
        return True
    return await relogin_and_heal(handler, browser_pool, platform_key, cancel_event)


async def relogin_after_auth_failure(
    handler: ReloginHandler | None,
    task: UrlTask,
    result: RecordResult,
    cancel_event: asyncio.Event,
) -> bool:
    """Return True when the user re-logged in and the task merits a retry."""

    if handler is None or cancel_event.is_set() or not is_login_failure(result):
        return False
    policy = auth_policy_for_url(task.normalized_url)
    if policy is None:
        return False
    # mark_access_invalid has already evicted the failing profile context on
    # every login-barrier path, so the retry page() loads the fresh state.
    return await handler(policy.platform_key, policy.display_name, cancel_event)
