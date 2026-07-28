from types import SimpleNamespace

import pytest

from src.config.settings import TaskConfig
from src.screenshot.browser import BrowserPool, _ContextSlot


class FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.closed = False

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeContext:
    def __init__(self, *, fail_storage: bool = False) -> None:
        self.fail_storage = fail_storage
        self.storage_calls = 0
        self.closed = False

    async def storage_state(self, **_kwargs: object) -> dict:
        self.storage_calls += 1
        if self.fail_storage:
            raise RuntimeError("Connection closed while reading from the driver")
        return {"cookies": [], "origins": []}

    async def close(self) -> None:
        self.closed = True


class FakeStore:
    @staticmethod
    def profile_for(_platform_key: str):
        return SimpleNamespace(validation_url="https://example.test")

    def commit_validated_state(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_cancelled_close_skips_login_state_refresh() -> None:
    context = FakeContext()
    browser = FakeBrowser()
    playwright = FakePlaywright()
    pool = BrowserPool(TaskConfig(), auth_store=FakeStore())
    pool._browser = browser
    pool._playwright = playwright
    pool._contexts["profile:test"] = _ContextSlot(
        context,
        "profile:test",
        "zhihu",
        "profile",
        validated=True,
    )

    await pool.close_for_cancellation()

    assert context.storage_calls == 0
    assert context.closed
    assert browser.closed
    assert playwright.stopped


@pytest.mark.asyncio
async def test_connection_loss_stops_refreshing_remaining_profiles() -> None:
    first = FakeContext(fail_storage=True)
    second = FakeContext()
    pool = BrowserPool(TaskConfig(), auth_store=FakeStore())
    pool._browser = FakeBrowser()
    pool._playwright = FakePlaywright()
    pool._contexts = {
        "profile:first": _ContextSlot(
            first,
            "profile:first",
            "zhihu",
            "profile",
            validated=True,
        ),
        "profile:second": _ContextSlot(
            second,
            "profile:second",
            "weibo",
            "profile",
            validated=True,
        ),
    }

    await pool.close()

    assert first.storage_calls == 1
    assert second.storage_calls == 0
