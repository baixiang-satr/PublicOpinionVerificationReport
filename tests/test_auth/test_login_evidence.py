from threading import Event

import pytest

from src.auth.login_evidence import (
    state_has_authenticated_session,
    wait_for_login_evidence,
)


def test_strong_account_cookie_distinguishes_login_from_guest_devices() -> None:
    guest = {
        "cookies": [
            {"name": "buvid3", "value": "device", "domain": ".bilibili.com"},
            {"name": "bili_ticket", "value": "ticket", "domain": ".bilibili.com"},
        ]
    }
    signed_in = {
        "cookies": [
            *guest["cookies"],
            {"name": "SESSDATA", "value": "secret", "domain": ".bilibili.com"},
        ]
    }

    assert state_has_authenticated_session("bilibili", guest) is False
    assert state_has_authenticated_session("bilibili", signed_in) is True
    assert state_has_authenticated_session("wechat_video", guest) is False
    assert state_has_authenticated_session(
        "wechat_video",
        {"cookies": [{"name": "sessionid", "value": "not-inspected"}]},
    ) is True


class _Page:
    async def evaluate(self, _script: str):
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def is_closed(self) -> bool:
        return False


class _Context:
    def __init__(self, page: _Page, states: list[dict]) -> None:
        self.pages = [page]
        self._states = states
        self._index = 0

    async def storage_state(self, **_kwargs: object) -> dict:
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1
        return state


@pytest.mark.asyncio
async def test_saved_cookie_alone_does_not_close_new_login_window() -> None:
    page = _Page()
    saved = {
        "cookies": [
            {"name": "web_session", "value": "old", "domain": ".xiaohongshu.com"}
        ],
        "origins": [],
    }
    context = _Context(page, [saved])

    authenticated = await wait_for_login_evidence(
        context,
        page,
        "xiaohongshu",
        3,
        None,
        baseline_state=saved,
    )

    assert authenticated is False


@pytest.mark.asyncio
async def test_new_account_cookie_after_page_load_completes_login() -> None:
    page = _Page()
    guest = {"cookies": [], "origins": []}
    signed_in = {
        "cookies": [
            {"name": "web_session", "value": "new", "domain": ".xiaohongshu.com"}
        ],
        "origins": [],
    }
    context = _Context(page, [signed_in])

    authenticated = await wait_for_login_evidence(
        context,
        page,
        "xiaohongshu",
        4,
        None,
        baseline_state=guest,
    )

    assert authenticated is True


@pytest.mark.asyncio
async def test_refreshed_account_cookie_completes_explicit_relogin() -> None:
    page = _Page()
    saved = {
        "cookies": [
            {"name": "web_session", "value": "old", "domain": ".xiaohongshu.com"}
        ],
        "origins": [],
    }
    refreshed = {
        "cookies": [
            {"name": "web_session", "value": "new", "domain": ".xiaohongshu.com"}
        ],
        "origins": [],
    }
    context = _Context(page, [refreshed])

    authenticated = await wait_for_login_evidence(
        context,
        page,
        "xiaohongshu",
        4,
        None,
        baseline_state=saved,
    )

    assert authenticated is True


@pytest.mark.asyncio
async def test_closing_the_only_login_page_stops_waiting_immediately() -> None:
    page = _Page()
    context = _Context(page, [{"cookies": [], "origins": []}])
    context.pages = []

    authenticated = await wait_for_login_evidence(
        context,
        page,
        "xiaohongshu",
        90,
        None,
        baseline_state={"cookies": [], "origins": []},
    )

    assert authenticated is False
    assert context._index == 1


@pytest.mark.asyncio
async def test_account_cookie_waits_for_explicit_operator_confirmation() -> None:
    page = _Page()
    guest = {"cookies": [], "origins": []}
    signed_in = {
        "cookies": [
            {"name": "SESSDATA", "value": "new", "domain": ".bilibili.com"}
        ],
        "origins": [],
    }
    confirmation = Event()

    authenticated = await wait_for_login_evidence(
        _Context(page, [signed_in]),
        page,
        "bilibili",
        3,
        None,
        baseline_state=guest,
        confirmation_event=confirmation,
    )
    assert authenticated is False

    confirmation.set()
    authenticated = await wait_for_login_evidence(
        _Context(page, [signed_in]),
        page,
        "bilibili",
        3,
        None,
        baseline_state=guest,
        confirmation_event=confirmation,
    )
    assert authenticated is True
