"""U04：复验 js_api（列表/开始/取消/批量删除）与单条探测分类。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.domain.models import PageData, RecordResult, RecordStatus, UrlTask
from src.services.url_recheck import RECHECK_FILENAME, RecheckStatus, UrlRecheckStore
from src.tools.page_access import AccessBarrier, AccessKind
from src.webui import recheck_runner
from src.webui.recheck_api import RecheckApiMixin
from src.webui.recheck_runner import RecheckRunner
from src.webui.runner import EventSink


def _record(evidence_id: int, url: str) -> RecordResult:
    return RecordResult(
        task=UrlTask(evidence_id, url, url),
        status=RecordStatus.EXPORTED,
        page=PageData(final_url=url),
    )


class _FakeSession:
    def __init__(self, job_dir: Path, records: list[RecordResult]) -> None:
        self.job_dir = job_dir
        self._records = records
        self.removed: list[int] = []

    def records(self) -> list[RecordResult]:
        return list(self._records)

    def evidence_ids(self) -> list[int]:
        return [record.task.evidence_id for record in self._records]

    def remove_record(self, evidence_id: int) -> bool:
        remaining = [
            record for record in self._records if record.task.evidence_id != evidence_id
        ]
        if len(remaining) == len(self._records):
            return False
        self.removed.append(evidence_id)
        self._records = remaining
        return True


class _FakeAuth:
    def store(self) -> None:
        return None


class _Host(RecheckApiMixin):
    def __init__(self, session: _FakeSession | None) -> None:
        self._session_obj = session
        self._sink = EventSink()

    def _session(self) -> _FakeSession | None:
        return self._session_obj

    def _current_config(self) -> AppConfig:
        return AppConfig(
            template=TemplateConfig(source_dir=Path("."), output_dir=Path(".")),
            task=TaskConfig(),
        )

    @property
    def auth(self) -> _FakeAuth:
        return _FakeAuth()


def test_list_url_recheck_joins_session_and_store(tmp_path: Path) -> None:
    session = _FakeSession(tmp_path, [_record(1, "https://a.test/1"), _record(2, "")])
    UrlRecheckStore(tmp_path).set(1, RecheckStatus.INVALID, "CONTENT_NOT_FOUND", "404")
    host = _Host(session)

    payload = host.list_url_recheck()

    assert payload["ok"] is True
    assert payload["running"] is False
    assert [row["eid"] for row in payload["rows"]] == [1, 2]
    assert payload["rows"][0]["recheck"]["status"] == "invalid"
    assert payload["rows"][1]["recheck"] is None


def test_list_url_recheck_without_session(tmp_path: Path) -> None:
    host = _Host(None)
    payload = host.list_url_recheck()
    assert payload == {"ok": False, "rows": [], "running": False}


def test_remove_records_batch_and_prune(tmp_path: Path) -> None:
    session = _FakeSession(tmp_path, [_record(1, "https://a.test/1"), _record(2, "https://a.test/2")])
    UrlRecheckStore(tmp_path).set(1, RecheckStatus.INVALID)
    UrlRecheckStore(tmp_path).set(2, RecheckStatus.VALID)
    host = _Host(session)

    result = host.remove_records([1, "bad", 999])

    assert result == {"ok": True, "removed": 1}
    assert session.removed == [1]
    reloaded = UrlRecheckStore(tmp_path)
    assert reloaded.get(1) is None  # 已删记录的复验结果联动清理
    assert reloaded.get(2) is not None


def test_remove_records_without_session() -> None:
    host = _Host(None)
    assert host.remove_records([1]) == {"ok": False, "removed": 0}


def test_start_recheck_rejects_when_no_urls(tmp_path: Path) -> None:
    session = _FakeSession(tmp_path, [_record(1, "")])
    runner = RecheckRunner(lambda: _Host(session)._current_config(), EventSink())

    ok, message = runner.start(session, None)  # type: ignore[arg-type]

    assert ok is False
    assert "没有可复验" in message


class _FakePage:
    def __init__(self, status: int | None, *, fail: bool = False) -> None:
        self._status = status
        self._fail = fail
        self.url = "https://a.test/final"

    async def goto(self, url: str, **_options: Any) -> SimpleNamespace | None:
        if self._fail:
            raise TimeoutError("timeout")
        return SimpleNamespace(status=self._status)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False

    async def new_page(self) -> _FakePage:
        return self._page

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._context = _FakeContext(page)

    async def new_context(self, **_options: Any) -> _FakeContext:
        return self._context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, RecheckStatus.INVALID),
        (403, RecheckStatus.UNCERTAIN),
        (200, RecheckStatus.VALID),
    ],
)
async def test_probe_classifies_http_and_page(status: int, expected: RecheckStatus) -> None:
    runner = RecheckRunner(lambda: None, EventSink())
    browser = _FakeBrowser(_FakePage(status))
    config = TaskConfig(enable_stealth=False, page_stabilize_milliseconds=0)

    result = await runner._probe(browser, config, None, "https://a.test/1")

    assert result[0] is expected


@pytest.mark.asyncio
async def test_probe_navigation_failure_is_uncertain() -> None:
    runner = RecheckRunner(lambda: None, EventSink())
    browser = _FakeBrowser(_FakePage(None, fail=True))
    config = TaskConfig(enable_stealth=False, page_stabilize_milliseconds=0)

    status, code, _message = await runner._probe(browser, config, None, "https://a.test/1")

    assert status is RecheckStatus.UNCERTAIN
    assert code == "NAVIGATION_FAILED"


@pytest.mark.asyncio
async def test_probe_page_barrier_uses_three_way_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecheckRunner(lambda: None, EventSink())
    browser = _FakeBrowser(_FakePage(200))
    config = TaskConfig(enable_stealth=False, page_stabilize_milliseconds=0)

    async def _login_barrier(_page: Any, _final: str, _original: str) -> AccessBarrier:
        return AccessBarrier(
            AccessKind.LOGIN, "LOGIN_REQUIRED", "需要登录", RecordStatus.NEEDS_REVIEW
        )

    monkeypatch.setattr(recheck_runner, "inspect_page_access", _login_barrier)

    status, code, _message = await runner._probe(browser, config, None, "https://a.test/1")

    assert status is RecheckStatus.UNCERTAIN
    assert code == "LOGIN_REQUIRED"


@pytest.mark.asyncio
async def test_probe_repolls_until_late_hydrated_deleted_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPA 删除错误框晚水合：初检无屏障时有界重检，命中即判失效（命中即停）。"""

    runner = RecheckRunner(lambda: None, EventSink())
    browser = _FakeBrowser(_FakePage(200))
    config = TaskConfig(enable_stealth=False, page_stabilize_milliseconds=0)
    monkeypatch.setattr(recheck_runner, "_UNAVAILABLE_RECHECK_DELAY_MS", 0)
    calls = 0

    async def _late_barrier(
        _page: Any, _final: str, _original: str
    ) -> AccessBarrier | None:
        nonlocal calls
        calls += 1
        if calls < 2:
            return None
        return AccessBarrier(
            AccessKind.CONTENT_UNAVAILABLE,
            "CONTENT_UNAVAILABLE",
            "平台明确提示内容不存在、已删除或已下线；请核对原始 URL。",
            RecordStatus.FAILED,
        )

    monkeypatch.setattr(recheck_runner, "inspect_page_access", _late_barrier)

    status, code, _message = await runner._probe(browser, config, None, "https://a.test/1")

    assert status is RecheckStatus.INVALID
    assert code == "CONTENT_UNAVAILABLE"
    assert calls == 2


@pytest.mark.asyncio
async def test_probe_repoll_is_bounded_for_genuinely_valid_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持续无屏障时重检次数有界（首检 1 次 + 重检 N 次），最终判有效。"""

    runner = RecheckRunner(lambda: None, EventSink())
    browser = _FakeBrowser(_FakePage(200))
    config = TaskConfig(enable_stealth=False, page_stabilize_milliseconds=0)
    monkeypatch.setattr(recheck_runner, "_UNAVAILABLE_RECHECK_ATTEMPTS", 2)
    monkeypatch.setattr(recheck_runner, "_UNAVAILABLE_RECHECK_DELAY_MS", 0)
    calls = 0

    async def _no_barrier(_page: Any, _final: str, _original: str) -> None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(recheck_runner, "inspect_page_access", _no_barrier)

    status, _code, _message = await runner._probe(browser, config, None, "https://a.test/1")

    assert status is RecheckStatus.VALID
    assert calls == 3


def test_recheck_file_lives_in_job_dir(tmp_path: Path) -> None:
    UrlRecheckStore(tmp_path).set(7, RecheckStatus.VALID)
    assert (tmp_path / RECHECK_FILENAME).is_file()
    assert asyncio  # 保持 asyncio 导入供异步用例使用


class _FakePlaywrightHandle:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = SimpleNamespace(launch=self._launch)
        self._browser = browser

    async def _launch(self, **_options: Any) -> _FakeBrowser:
        return self._browser

    async def stop(self) -> None:
        return None


class _FakePlaywrightFactory:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser

    async def start(self) -> _FakePlaywrightHandle:
        return _FakePlaywrightHandle(self._browser)


def _wait_runner_idle(runner: RecheckRunner, timeout: float = 15.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while runner.is_running() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_recheck_runner_can_restart_after_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复验完成（done 事件发出）后必须能再次启动（只能点一次的竞态回归）。"""

    browser = _FakeBrowser(_FakePage(200))
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: _FakePlaywrightFactory(browser),
    )
    session = _FakeSession(tmp_path, [_record(1, "https://a.test/1")])
    config = AppConfig(
        template=TemplateConfig(source_dir=Path("."), output_dir=Path(".")),
        task=TaskConfig(
            enable_stealth=False,
            page_stabilize_milliseconds=0,
            min_host_interval_seconds=0,
        ),
    )
    runner = RecheckRunner(lambda: config, EventSink())

    ok, message = runner.start(session, None)  # type: ignore[arg-type]
    assert ok is True, message
    _wait_runner_idle(runner)
    assert runner.is_running() is False

    ok, message = runner.start(session, None)  # type: ignore[arg-type]
    assert ok is True, message
    _wait_runner_idle(runner)
    assert runner.is_running() is False
    entry = UrlRecheckStore(tmp_path).get(1)
    assert entry is not None
    assert entry.status is RecheckStatus.VALID
