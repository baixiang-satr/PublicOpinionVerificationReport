"""Offline tests for the persistent capture session (fakes, no real browser)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.screenshot.capture_session import GUEST_KEY, CaptureSession
from src.screenshot.region_capture_scripts import BINDING_NAME


class FakePage:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


class FakeContext:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}
        self.pages: list[FakePage] = []
        self.bindings: dict[str, Any] = {}
        self.closed = False
        self.storage_calls = 0

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    async def add_init_script(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def expose_binding(self, name: str, handler: Any) -> None:
        self.bindings[name] = handler

    async def storage_state(self, indexed_db: bool = False) -> dict:
        self.storage_calls += 1
        return {
            "cookies": [{"name": "sessionid", "domain": ".douyin.com", "value": "x"}],
            "origins": [],
        }

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self.closed = False

    def is_connected(self) -> bool:
        return not self.closed

    async def new_context(self, **kwargs: Any) -> FakeContext:
        context = FakeContext(kwargs)
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


def _session(tmp_path: Path, browser: FakeBrowser) -> CaptureSession:
    session = CaptureSession(TaskConfig(), AuthProfileStore(tmp_path / "auth"))
    session._browser = browser  # 测试接缝：跳过 playwright 启动
    return session


def _commit_valid(store: AuthProfileStore, platform_key: str) -> None:
    result = AuthProbeResult(
        platform_key=platform_key,
        status=AuthStatus.VALID,
        checked_at=datetime.now().astimezone(),
        original_url="https://www.douyin.com/video/1",
        message="ok",
        used_saved_state=True,
    )
    store.commit_validated_state(
        platform_key,
        {"cookies": [{"name": "old", "domain": ".douyin.com", "value": "y"}]},
        result,
    )


@pytest.mark.asyncio
async def test_context_reused_per_platform_and_binding_exposed_once(
    tmp_path: Path,
) -> None:
    browser = FakeBrowser()
    session = _session(tmp_path, browser)

    douyin_a = await session.context_for("douyin", {"cookies": []})
    douyin_b = await session.context_for("douyin", None)
    zhihu = await session.context_for("zhihu", None)
    guest = await session.context_for(GUEST_KEY, None)

    assert douyin_a is douyin_b  # 会话内复用，Cookie 留在内存
    assert douyin_a is not zhihu
    assert douyin_a is not guest
    assert len(browser.contexts) == 3
    assert list(douyin_a.bindings) == [BINDING_NAME]  # 只暴露一次


@pytest.mark.asyncio
async def test_kuaishou_author_context_can_force_desktop_user_agent(
    tmp_path: Path,
) -> None:
    browser = FakeBrowser()
    session = _session(tmp_path, browser)

    context = await session.context_for(
        "kuaishou",
        None,
        force_desktop=True,
    )

    assert "user_agent" not in context.options


@pytest.mark.asyncio
async def test_browse_page_reused_until_closed(tmp_path: Path) -> None:
    browser = FakeBrowser()
    session = _session(tmp_path, browser)
    context = await session.context_for("douyin", None)

    page_a = await session.browse_page_for("douyin", context)
    page_b = await session.browse_page_for("douyin", context)
    assert page_a is page_b

    page_a.closed = True  # 用户关窗 → 下次新建页面，context 仍存活
    page_c = await session.browse_page_for("douyin", context)
    assert page_c is not page_a
    assert len(context.pages) == 2


@pytest.mark.asyncio
async def test_save_states_refreshes_only_valid_profiles(tmp_path: Path) -> None:
    browser = FakeBrowser()
    session = _session(tmp_path, browser)
    store = AuthProfileStore(tmp_path / "auth")
    _commit_valid(store, "douyin")

    douyin = await session.context_for("douyin", None)
    zhihu = await session.context_for("zhihu", None)  # 无 VALID 档案

    await session.save_states()

    assert douyin.storage_calls == 1  # VALID 档案被刷新
    assert zhihu.storage_calls == 0  # 非 VALID 不写入，避免误判登录
    profile = store.profile_for("douyin")
    assert profile.status == AuthStatus.VALID


@pytest.mark.asyncio
async def test_binding_dispatch_routes_to_active_handler(tmp_path: Path) -> None:
    browser = FakeBrowser()
    session = _session(tmp_path, browser)
    context = await session.context_for("douyin", None)
    received: list[tuple[dict, Any]] = []

    async def handler(source: dict, payload: Any) -> None:
        received.append((source, payload))

    session.set_binding_handler(handler)
    await context.bindings[BINDING_NAME]({"page": object()}, '{"action":"cancel"}')
    session.set_binding_handler(None)
    await context.bindings[BINDING_NAME]({"page": object()}, '{"action":"cancel"}')

    assert len(received) == 1  # 清空后不再派发


@pytest.mark.asyncio
async def test_close_persists_and_tears_down(tmp_path: Path) -> None:
    browser = FakeBrowser()
    session = _session(tmp_path, browser)
    store = AuthProfileStore(tmp_path / "auth")
    _commit_valid(store, "douyin")
    context = await session.context_for("douyin", None)

    await session.close()

    assert context.closed
    assert browser.closed
    assert context.storage_calls == 1  # 关闭前回写登录态


def test_session_without_store_skips_persistence(tmp_path: Path) -> None:
    session = CaptureSession(TaskConfig(), None)
    asyncio.run(session.save_states())  # 不抛异常即通过
