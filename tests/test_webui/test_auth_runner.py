"""AuthRunner 测试：验证超时兜底、验证中取消、仅复验本次涉及平台（全离线）。"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from src.auth.models import AuthProbeResult, AuthStatus
from src.config.settings import TaskConfig
from src.webui.auth_runner import AuthRunner
from src.webui.runner import EventSink


class _CollectingSink(EventSink):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        self.events.append((event_type, payload or {}))

    def auth_payloads(self) -> list[dict]:
        return [payload for kind, payload in self.events if kind == "auth"]


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _runner(tmp_path: Path, sink: _CollectingSink, **kwargs) -> AuthRunner:
    config = TaskConfig(auth_store_dir=tmp_path / "auth")
    return AuthRunner(lambda: config, sink, **kwargs)


def _result(platform_key: str, status: AuthStatus = AuthStatus.VALID) -> AuthProbeResult:
    return AuthProbeResult(
        platform_key=platform_key,
        status=status,
        checked_at=datetime.now().astimezone(),
        original_url="https://example.com/",
        message="ok",
    )


class _HangingService:
    """probe 永不返回，模拟卡死（浏览器启动/导航无响应）的验证。"""

    async def probe(self, platform_key, **kwargs):
        del platform_key, kwargs
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class _RecordingService:
    def __init__(self) -> None:
        self.probed: list[str] = []

    async def probe(self, platform_key, **kwargs):
        del kwargs
        self.probed.append(platform_key)
        return _result(platform_key)


def test_probe_timeout_emits_retryable_error(tmp_path: Path, monkeypatch) -> None:
    sink = _CollectingSink()
    runner = _runner(tmp_path, sink, probe_timeout_seconds=0.05)
    monkeypatch.setattr(runner, "_service", lambda: _HangingService())

    ok, _ = runner.start("probe", "weibo")
    assert ok
    assert _wait_until(lambda: not runner.is_running()), "超时兜底应结束验证线程"

    payloads = sink.auth_payloads()
    assert payloads, "超时后应向 UI 回吐结果"
    assert "验证超时" in payloads[-1]["message"]
    assert payloads[-1]["key"] == "weibo"


def test_cancel_stops_hanging_probe(tmp_path: Path, monkeypatch) -> None:
    sink = _CollectingSink()
    runner = _runner(tmp_path, sink, probe_timeout_seconds=3600.0)
    monkeypatch.setattr(runner, "_service", lambda: _HangingService())

    ok, _ = runner.start("probe", "weibo")
    assert ok
    ok, message = runner.cancel_login("weibo")
    assert ok
    assert "已取消本次验证" in message
    assert _wait_until(lambda: not runner.is_running()), "取消应立即中断卡住的验证"


def test_cancel_rejects_platform_without_operation(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _CollectingSink())
    ok, _ = runner.cancel_login("weibo")
    assert not ok


def test_confirm_login_rejects_non_login_action(tmp_path: Path, monkeypatch) -> None:
    sink = _CollectingSink()
    runner = _runner(tmp_path, sink, probe_timeout_seconds=5.0)
    monkeypatch.setattr(runner, "_service", lambda: _HangingService())
    assert runner.start("probe", "weibo")[0]
    ok, _ = runner.confirm_login("weibo")
    assert not ok
    runner.cancel_login("weibo")
    assert _wait_until(lambda: not runner.is_running())


def test_probe_relevant_only_touches_input_platforms(tmp_path: Path, monkeypatch) -> None:
    sink = _CollectingSink()
    service = _RecordingService()
    runner = _runner(tmp_path, sink, relevant_keys_getter=lambda: {"weibo", "zhihu"})
    monkeypatch.setattr(runner, "_service", lambda: service)

    ok, _ = runner.start("probe_relevant")
    assert ok
    assert _wait_until(lambda: not runner.is_running())

    assert sorted(service.probed) == ["weibo", "zhihu"]
    probing = [p for p in sink.auth_payloads() if p["status"] == "probing"]
    assert {p["key"] for p in probing} == {"weibo", "zhihu"}


def test_probe_relevant_without_input_platforms_is_noop(tmp_path: Path, monkeypatch) -> None:
    sink = _CollectingSink()
    service = _RecordingService()
    runner = _runner(tmp_path, sink, relevant_keys_getter=set)
    monkeypatch.setattr(runner, "_service", lambda: service)

    ok, _ = runner.start("probe_relevant")
    assert ok
    assert _wait_until(lambda: not runner.is_running())
    assert service.probed == []
