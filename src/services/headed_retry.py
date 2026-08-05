"""Bounded headed-browser retry for navigation-class failures.

Headless crawling can be jammed by platform anti-bot walls (douyin slider,
login modal, silent render hangs).  When the main pass leaves records with
navigation/access errors, this module re-runs just those records once with
a visible browser: the existing ``wait_for_manual_access`` window gives the
user up to ``manual_intervention_timeout_seconds`` to solve a slider or
scan a login QR, and the pool's shutdown hook persists any refreshed login
state.  Everything is bounded — one extra pass over the failed subset, same
per-record timeouts — and disabled via ``enable_headed_fallback=False``.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any, Callable

from src.config.settings import TaskConfig
from src.domain.models import RecordResult, RecordStatus

logger = logging.getLogger(__name__)

ELIGIBLE_CODES = frozenset(
    {
        "PAGE_PROCESSING_TIMEOUT",
        "NAVIGATION_TIMEOUT",
        "NAVIGATION_FAILED",
        "NAVIGATION_PARTIAL_TIMEOUT",
        "LOGIN_REQUIRED",
        "CAPTCHA_REQUIRED",
        "ACCESS_CHALLENGE",
        "EMPTY_RENDERED_PAGE",
        "AUTHOR_SCREENSHOT_REQUIRED",
    }
)

ELIGIBLE_STATUSES = frozenset({RecordStatus.FAILED, RecordStatus.NEEDS_REVIEW})


def eligible_records(records: list[RecordResult]) -> list[RecordResult]:
    """Records whose failure a visible browser + human moment can plausibly fix."""

    return [
        record
        for record in records
        if record.status in ELIGIBLE_STATUSES
        and any(error.code in ELIGIBLE_CODES for error in record.errors)
    ]


async def retry_failed_records_headed(
    records: list[RecordResult],
    *,
    task_config: TaskConfig,
    engine_factory: Callable[[TaskConfig], Any],
    output_dir: Path,
    on_event: Callable | None,
    on_result: Callable | None,
    cancel_event: asyncio.Event,
    log: Callable[[str, str], None] | None = None,
) -> int:
    """Re-run eligible failed records once with a headed browser, in place.

    Returns the number of records retried.  Results replace the original
    records in *records* (last attempt wins, keeping the freshest
    diagnostics); order and evidence ids are preserved.
    """

    candidates = eligible_records(records)
    if not candidates:
        return 0
    if cancel_event.is_set():
        return 0
    if log is not None:
        log(
            "INFO",
            f"{len(candidates)} 条记录未完成，将打开可见浏览器重试；"
            f"如遇滑块或登录页，请在 {task_config.manual_intervention_timeout_seconds} 秒内手动完成。",
        )
    headed_config = replace(task_config, headless=False)
    engine = engine_factory(headed_config)
    retried = await engine.run(
        [record.task for record in candidates],
        output_dir,
        on_event=on_event,
        on_result=on_result,
        cancel_event=cancel_event,
    )
    by_id = {record.task.evidence_id: record for record in retried}
    replaced = 0
    for index, record in enumerate(records):
        replacement = by_id.get(record.task.evidence_id)
        if replacement is not None:
            records[index] = replacement
            replaced += 1
    if log is not None:
        recovered = sum(
            1
            for record in by_id.values()
            if record.status not in ELIGIBLE_STATUSES
        )
        log("INFO", f"有头兜底重试结束：{replaced} 条重试，{recovered} 条恢复。")
    return replaced
