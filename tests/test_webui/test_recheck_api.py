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


def test_recheck_file_lives_in_job_dir(tmp_path: Path) -> None:
    UrlRecheckStore(tmp_path).set(7, RecheckStatus.VALID)
    assert (tmp_path / RECHECK_FILENAME).is_file()
    assert asyncio  # 保持 asyncio 导入供异步用例使用
