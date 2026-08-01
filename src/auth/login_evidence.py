"""Conservative evidence that a saved browser state is actually signed in.

Opening a public article without a login wall is not proof of authentication.
The auth manager used to treat that situation as VALID and could therefore
save a guest cookie jar.  This module keeps a small, auditable set of strong
cookie-name signals for platforms where the distinction matters most.

Cookie *values* are never inspected or logged.  ``None`` means that the
platform has no reliable static signature here and the regular rendered-page
probe remains authoritative; ``False`` means a known signature is missing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
import json
from threading import Event
from typing import Any


# Any one name in the platform set is strong evidence of an authenticated
# account session.  These are durable account/session cookie names rather
# than anonymous device, analytics or anti-bot cookies.
_AUTH_COOKIE_NAMES: dict[str, frozenset[str]] = {
    "baijiahao": frozenset({"bduss", "bduss_bfess", "stoken", "ptoken"}),
    "bilibili": frozenset({"sessdata", "dedeuserid", "bili_jct"}),
    "douyin": frozenset({"sessionid", "sessionid_ss", "uid_tt", "sid_tt"}),
    "kuaishou": frozenset(
        {
            "userid",
            "passtoken",
            "kuaishou.server.web_st",
            "kuaishou.server.web_ph",
            "kuaishou.web.cp.api_st",
        }
    ),
    "sohu_video": frozenset(
        {"ppinf", "ppinfo", "passport", "pprdig", "ppmdig"}
    ),
    # The official 视频号助手 frontend explicitly redirects to /login.html
    # whenever this account cookie is absent.  Analytics localStorage alone
    # is therefore never sufficient login evidence.
    "wechat_video": frozenset({"sessionid"}),
    "toutiao": frozenset({"sessionid", "sessionid_ss", "uid_tt", "sid_tt"}),
    "netease_news": frozenset({"p_info", "ntes_sess", "s_info", "ntes_yd_sess"}),
    "weibo": frozenset({"sub", "subp", "wbpsess"}),
    "xiaohongshu": frozenset({"web_session", "id_token"}),
    "ixigua": frozenset({"sessionid", "sessionid_ss", "uid_tt", "sid_tt"}),
}


def state_has_authenticated_session(
    platform_key: str,
    storage_state: Mapping[str, Any] | None,
) -> bool | None:
    """Return strong static login evidence for *platform_key*.

    ``True``/``False`` are returned only for platforms with a known signature.
    Unknown platforms return ``None`` so a false negative cannot block them.
    """

    expected = _AUTH_COOKIE_NAMES.get(platform_key)
    if expected is None:
        return None
    if not isinstance(storage_state, Mapping):
        return False
    cookies = storage_state.get("cookies", ())
    if not isinstance(cookies, (list, tuple)):
        return False
    names = {
        str(cookie.get("name") or "").casefold()
        for cookie in cookies
        if isinstance(cookie, Mapping)
    }
    return bool(names.intersection(expected))


def login_evidence_required(platform_key: str) -> bool:
    """Whether this platform has a known strong static login signature."""

    return platform_key in _AUTH_COOKIE_NAMES


async def page_authenticated_ui_state(page: Any) -> bool | None:
    """Return explicit rendered login/logout UI evidence when available."""

    if not hasattr(page, "evaluate"):
        return None
    try:
        result = await page.evaluate(
            r"""() => {
                const visible = (element) => {
                  if (!element) return false;
                  const style = getComputedStyle(element);
                  const rect = element.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
                };
                const elements = Array.from(document.querySelectorAll(
                  'a, button, [role="button"], [aria-label], [title]'
                )).filter(visible);
                const labels = elements.map(element => (
                  element.getAttribute('aria-label')
                  || element.getAttribute('title')
                  || element.innerText
                  || element.textContent
                  || ''
                ).trim().replace(/\s+/g, ' ').slice(0, 40));
                const positive = labels.some(label =>
                  /^(退出登录|退出账号|注销|log out|sign out)$/i.test(label)
                );
                if (positive) return true;
                const loginField = Array.from(document.querySelectorAll(
                  'input[type="password"], input[type="tel"], '
                  + 'input[autocomplete="one-time-code"], '
                  + 'input[placeholder*="手机号"], input[placeholder*="验证码"]'
                )).some(visible);
                if (loginField) return false;
                const negative = labels.some(label =>
                  /^(登录|立即登录|登录\/注册|注册\/登录|sign in|log in)$/i.test(label)
                );
                return negative ? false : null;
            }"""
        )
    except Exception:
        return None
    return result if isinstance(result, bool) else None


async def wait_for_login_evidence(
    context: Any,
    page: Any,
    platform_key: str,
    timeout_seconds: int,
    cancel_event: Event | None,
    *,
    baseline_state: Mapping[str, Any] | None = None,
    confirmation_event: Event | None = None,
) -> bool:
    """Wait until the one visible login page produces account-level state.

    Known platforms use durable account-cookie signatures.  For platforms
    without a stable public cookie contract, an explicit signed-in UI or a
    storage-state change after the login page was shown is accepted.

    When ``confirmation_event`` is supplied, evidence is never accepted until
    the operator explicitly confirms completion in the auth manager.  This
    prevents redirects, delayed device cookies, and already-signed-in landing
    pages from making a visible login window appear and immediately vanish.
    """

    remaining = max(0, int(timeout_seconds))
    baseline_fingerprint = _state_fingerprint(baseline_state)
    baseline_static = state_has_authenticated_session(
        platform_key,
        baseline_state,
    )
    baseline_auth_fingerprint = _authenticated_cookie_fingerprint(
        platform_key,
        baseline_state,
    )
    observed_seconds = 0
    while remaining > 0:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        try:
            state = await context.storage_state(indexed_db=True)
        except Exception:  # noqa: BLE001 - a closing browser cannot authenticate
            return False
        static = state_has_authenticated_session(platform_key, state)
        context_pages = getattr(context, "pages", None)
        if context_pages is None:
            pages = (page,)
        else:
            pages = tuple(
                item
                for item in context_pages
                if not _page_is_closed(item)
            )
            if not pages:
                # The operator closed the login window.  Stop immediately so
                # the browser/driver cannot linger for the remainder of the
                # manual-intervention timeout or surface replacement tabs.
                return False
        ui_states = [await page_authenticated_ui_state(item) for item in pages]
        signed_in_ui = any(value is True for value in ui_states)
        login_ui_visible = any(value is False for value in ui_states)
        state_changed = (
            baseline_fingerprint is not None
            and _state_fingerprint(state) != baseline_fingerprint
        )
        current_auth_fingerprint = _authenticated_cookie_fingerprint(
            platform_key,
            state,
        )
        auth_cookie_changed = (
            current_auth_fingerprint is not None
            and current_auth_fingerprint != baseline_auth_fingerprint
        )

        # Never close a just-opened browser on evidence that was already in
        # the saved baseline.  Give the login surface time to render, then
        # require either explicit signed-in UI or a new account/storage state
        # created during this login interaction.
        operator_confirmed = (
            confirmation_event is None or confirmation_event.is_set()
        )
        if observed_seconds >= 2 and operator_confirmed:
            if signed_in_ui:
                return True
            if (
                static is True
                and (baseline_static is not True or auth_cookie_changed)
            ):
                return True
            if state_changed and not login_ui_visible:
                if static is True or (static is None and baseline_static is None):
                    return True

        wait_page = pages[-1]
        try:
            await wait_page.wait_for_timeout(1_000)
        except Exception:  # noqa: BLE001 - popup replacement can close the old page
            await asyncio.sleep(1)
        remaining -= 1
        observed_seconds += 1
    return False


def _state_fingerprint(state: Mapping[str, Any] | None) -> str | None:
    if not isinstance(state, Mapping):
        return None
    try:
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _authenticated_cookie_fingerprint(
    platform_key: str,
    state: Mapping[str, Any] | None,
) -> str | None:
    expected = _AUTH_COOKIE_NAMES.get(platform_key)
    if expected is None or not isinstance(state, Mapping):
        return None
    cookies = state.get("cookies", ())
    if not isinstance(cookies, (list, tuple)):
        return None
    authenticated = [
        cookie
        for cookie in cookies
        if isinstance(cookie, Mapping)
        and str(cookie.get("name") or "").casefold() in expected
    ]
    return _state_fingerprint({"cookies": authenticated}) if authenticated else None


def _page_is_closed(page: Any) -> bool:
    check = getattr(page, "is_closed", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 - a disposed page is effectively closed
        return True
