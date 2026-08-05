"""Offline tests for the headed-fallback pass (fake engine, no browser)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.config.settings import TaskConfig
from src.domain.models import RecordResult, RecordStatus, TaskError, UrlTask
from src.services.headed_retry import eligible_records, retry_failed_records_headed


def _record(eid: int, status: RecordStatus, *codes: str) -> RecordResult:
    result = RecordResult(
        task=UrlTask(eid, f"https://example.test/{eid}", f"https://example.test/{eid}"),
        status=status,
    )
    for code in codes:
        result.errors.append(
            TaskError("navigation", code, "诊断", retryable=False)
        )
    return result


def test_eligible_records_filters_navigation_and_access_failures() -> None:
    timeout = _record(1, RecordStatus.FAILED, "PAGE_PROCESSING_TIMEOUT")
    parse = _record(2, RecordStatus.NEEDS_REVIEW, "PARSE_FAILED")
    login = _record(3, RecordStatus.NEEDS_REVIEW, "LOGIN_REQUIRED")
    ok = _record(4, RecordStatus.ASSETS_READY)
    no_errors = _record(5, RecordStatus.FAILED)

    eligible = eligible_records([timeout, parse, login, ok, no_errors])

    assert [record.task.evidence_id for record in eligible] == [1, 3]


def test_eligible_records_includes_required_author_screenshot() -> None:
    missing_author = _record(1, RecordStatus.NEEDS_REVIEW, "AUTHOR_SCREENSHOT_REQUIRED")

    eligible = eligible_records([missing_author])

    assert [record.task.evidence_id for record in eligible] == [1]


@pytest.mark.asyncio
async def test_retry_replaces_records_with_headed_results(tmp_path: Path) -> None:
    failed = _record(1, RecordStatus.FAILED, "PAGE_PROCESSING_TIMEOUT")
    ok = _record(2, RecordStatus.ASSETS_READY)
    records = [failed, ok]
    recovered = _record(1, RecordStatus.ASSETS_READY)
    logged: list[str] = []

    class FakeEngine:
        def __init__(self, config: TaskConfig) -> None:
            assert config.headless is False  # 兜底必须是有头浏览器

        async def run(
            self,
            tasks: Any,
            output_dir: Path,
            on_event: Any = None,
            on_result: Any = None,
            cancel_event: Any = None,
        ) -> list[RecordResult]:
            assert [task.evidence_id for task in tasks] == [1]
            return [recovered]

    retried = await retry_failed_records_headed(
        records,
        task_config=TaskConfig(),
        engine_factory=FakeEngine,
        output_dir=tmp_path,
        on_event=None,
        on_result=None,
        cancel_event=asyncio.Event(),
        log=lambda _level, message: logged.append(message),
    )

    assert retried == 1
    assert records[0] is recovered
    assert records[1] is ok
    assert any("可见浏览器" in message for message in logged)


@pytest.mark.asyncio
async def test_retry_noop_without_eligible_records(tmp_path: Path) -> None:
    records = [_record(1, RecordStatus.ASSETS_READY)]

    class ExplodingEngine:
        def __init__(self, _config: TaskConfig) -> None:
            raise AssertionError("must not be built")

    retried = await retry_failed_records_headed(
        records,
        task_config=TaskConfig(),
        engine_factory=ExplodingEngine,
        output_dir=tmp_path,
        on_event=None,
        on_result=None,
        cancel_event=asyncio.Event(),
    )

    assert retried == 0


@pytest.mark.asyncio
async def test_retry_noop_when_cancelled(tmp_path: Path) -> None:
    records = [_record(1, RecordStatus.FAILED, "PAGE_PROCESSING_TIMEOUT")]
    cancel_event = asyncio.Event()
    cancel_event.set()

    class ExplodingEngine:
        def __init__(self, _config: TaskConfig) -> None:
            raise AssertionError("must not be built")

    retried = await retry_failed_records_headed(
        records,
        task_config=TaskConfig(),
        engine_factory=ExplodingEngine,
        output_dir=tmp_path,
        on_event=None,
        on_result=None,
        cancel_event=cancel_event,
    )

    assert retried == 0
