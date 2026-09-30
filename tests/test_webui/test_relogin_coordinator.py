"""CrawlReloginCoordinator 测试：抓取中重登成功 / 跳过 / 串行（全离线）。"""
from __future__ import annotations

import asyncio

import pytest

from src.auth.models import AuthProfile, AuthStatus
import src.webui.relogin_coordinator as coordinator_module
from src.webui.relogin_coordinator import CrawlReloginCoordinator


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        if event_type == "auth_relogin":
            self.events.append(payload or {})

    def phases(self) -> list[str]:
        return [event["phase"] for event in self.events]


class _FakeStore:
    def __init__(self) -> None:
        self.status = AuthStatus.UNKNOWN
        self.statuses: dict[str, AuthStatus] = {}

    def profile_for(self, platform_key: str) -> AuthProfile:
        return AuthProfile(
            profile_id=f"{platform_key}-primary",
            platform_key=platform_key,
            auth_scope=platform_key,
            status=self.statuses.get(platform_key, self.status),
        )


class _FakeAuthRunner:
    """模拟交互登录线程：start 打开窗口，VALID/取消/超时由测试驱动。"""

    def __init__(self, store: _FakeStore) -> None:
        self._store = store
        self.running = False
        self.cancel_count = 0
        self.overlapped = False
        self.starts: list[str] = []

    def start(self, action: str, platform_key: str | None = None):
        assert action == "login"
        if self.running:
            self.overlapped = True
        self.running = True
        self.starts.append(str(platform_key))
        return True, ""

    def is_running(self) -> bool:
        return self.running

    def cancel(self) -> None:
        self.cancel_count += 1
        self.running = False

    def store(self) -> _FakeStore:
        return self._store


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coordinator_module, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(coordinator_module, "_DECISION_TIMEOUT_SECONDS", 5.0)


async def test_relogin_success_emits_waiting_then_done() -> None:
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)

    async def complete_login() -> None:
        await asyncio.sleep(0.05)
        store.status = AuthStatus.VALID
        auth.running = False

    finisher = asyncio.create_task(complete_login())
    assert await coordinator.relogin("weibo", "新浪微博", asyncio.Event()) is True
    await finisher
    assert sink.phases() == ["waiting", "done"]
    assert auth.cancel_count == 0


async def test_relogin_skip_cancels_login_window() -> None:
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)

    async def user_skips() -> None:
        await asyncio.sleep(0.05)
        coordinator.resume("weibo", "skip")

    skipper = asyncio.create_task(user_skips())
    assert await coordinator.relogin("weibo", "新浪微博", asyncio.Event()) is False
    await skipper
    assert auth.cancel_count >= 1
    assert sink.phases() == ["waiting", "done"]


async def test_relogin_failed_attempt_then_retry_succeeds() -> None:
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)

    async def user_flow() -> None:
        await asyncio.sleep(0.05)
        auth.running = False  # 第一次登录窗口超时/未通过
        await asyncio.sleep(0.05)
        coordinator.resume("weibo", "retry")
        await asyncio.sleep(0.05)
        store.status = AuthStatus.VALID
        auth.running = False

    driver = asyncio.create_task(user_flow())
    assert await coordinator.relogin("weibo", "新浪微博", asyncio.Event()) is True
    await driver
    assert auth.starts == ["weibo", "weibo"]
    assert sink.phases() == ["waiting", "failed", "waiting", "done"]


async def test_relogin_serializes_platforms() -> None:
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)

    async def complete_all() -> None:
        for _ in range(2):
            while not auth.running:
                await asyncio.sleep(0.01)
            # 只把当前开窗的平台刷回 VALID；另一平台仍未登录。
            store.statuses[auth.starts[-1]] = AuthStatus.VALID
            auth.running = False
            await asyncio.sleep(0.01)

    driver = asyncio.create_task(complete_all())
    results = await asyncio.gather(
        coordinator.relogin("weibo", "新浪微博", asyncio.Event()),
        coordinator.relogin("zhihu", "知乎", asyncio.Event()),
    )
    await driver
    assert results == [True, True]
    assert not auth.overlapped, "一次只能打开一个登录窗口"
    assert len(auth.starts) == 2


async def test_relogin_start_failure_can_retry() -> None:
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)
    busy = {"value": True}

    def start(action: str, platform_key: str | None = None):
        if busy["value"]:
            return False, "登录态操作正在进行中，请稍候。"
        return _FakeAuthRunner.start(auth, action, platform_key)

    auth.start = start  # type: ignore[method-assign]

    async def user_flow() -> None:
        await asyncio.sleep(0.05)
        busy["value"] = False
        coordinator.resume("weibo", "retry")
        await asyncio.sleep(0.1)
        store.status = AuthStatus.VALID
        auth.running = False

    driver = asyncio.create_task(user_flow())
    assert await coordinator.relogin("weibo", "新浪微博", asyncio.Event()) is True
    await driver


async def test_relogin_cancel_event_stops_waiting() -> None:
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)
    cancel = asyncio.Event()

    async def cancel_job() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    driver = asyncio.create_task(cancel_job())
    assert await coordinator.relogin("weibo", "新浪微博", cancel) is False
    await driver
    assert auth.cancel_count >= 1


async def test_relogin_short_circuits_when_profile_already_valid() -> None:
    """URL 并行下排队进锁时档案已被先前重登刷回 VALID → 不再弹窗。"""
    sink, store = _Sink(), _FakeStore()
    store.status = AuthStatus.VALID
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)

    assert await coordinator.relogin("douyin", "抖音", asyncio.Event()) is True

    assert auth.starts == []
    assert sink.phases() == ["done"]


async def test_relogin_parallel_same_platform_opens_window_once() -> None:
    """同平台多条 URL 同时判定登录失效：只开一次登录窗，其余短路放行。"""
    sink, store = _Sink(), _FakeStore()
    auth = _FakeAuthRunner(store)
    coordinator = CrawlReloginCoordinator(sink, auth)

    async def complete_login() -> None:
        while not auth.running:
            await asyncio.sleep(0.01)
        store.statuses["douyin"] = AuthStatus.VALID
        auth.running = False

    driver = asyncio.create_task(complete_login())
    results = await asyncio.gather(
        *(coordinator.relogin("douyin", "抖音", asyncio.Event()) for _ in range(3))
    )
    await driver
    assert results == [True, True, True]
    assert auth.starts == ["douyin"]
