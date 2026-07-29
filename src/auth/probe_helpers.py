"""Shared probe helpers for guest/auth validation flows in ``service.py``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from threading import Event
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus
from src.config.settings import TaskConfig
from src.crawler.navigation import stabilize_rendered_page
from src.tools.page_access import inspect_http_response, inspect_page_access


ProgressCallback = Callable[[str, AuthStatus, str], None]


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


async def _navigate_probe_candidates(
    page: Any,
    policy: Any,
    config: TaskConfig,
) -> tuple[Any | None, str]:
    """Navigate probe candidates in order, skipping dead probe pages.

    Returns ``(barrier, url)``.  A ``None`` barrier means a candidate
    rendered cleanly.  An explicit login wall (manual-recoverable or HTTP
    401) stops the iteration immediately; dead, deleted, empty or
    risk-blocked probe pages fall through to the next candidate because they
    say nothing about the login state itself.
    """

    last: tuple[Any | None, str] = (None, policy.probe_url)
    for candidate_url in policy.probe_candidates:
        response = await _navigate(page, candidate_url, config)
        barrier = inspect_http_response(
            int(response.status) if response is not None else None
        )
        if barrier is None:
            barrier = await inspect_page_access(
                page,
                str(page.url),
                candidate_url,
            )
        last = (barrier, candidate_url)
        if barrier is None:
            return last
        if barrier.manual_recoverable or barrier.code == "HTTP_401":
            return last
    return last


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
