"""Bounded parallel execution of one platform's URL queue.

``PlatformTaskScheduler.queues`` groups a batch by platform; this module
runs each group with up to ``TaskConfig.platform_url_concurrency`` URLs in
flight at once (``platform_url_concurrency=1`` restores the old strictly
serial behaviour).  The browser pool's own semaphore stays the global cap
and ``HostRateLimiter`` keeps pacing same-host navigations, so the request
rate a server observes does not change — only the local post-navigation
work (parse, screenshot, OCR) overlaps.

Platform-level gating is unchanged: the stored-auth block check runs once
before any URL starts, and the first login/captcha barrier pauses every URL
that has not acquired the queue semaphore yet; URLs already in flight
finish normally.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.crawler.engine_events import emit_event
from src.crawler.platform_scheduler import PlatformTaskScheduler
from src.crawler.relogin import (
    ReloginHandler,
    heal_or_relogin,
    relogin_after_auth_failure,
)
from src.domain.models import (
    RecordResult,
    RecordStatus,
    TaskError,
    TaskEvent,
    UrlTask,
)
from src.utils.time_utils import DEFAULT_TIMEZONE

logger = logging.getLogger(__name__)

ProcessFn = Callable[
    [
        UrlTask,
        Path,
        "Callable[[TaskEvent], None] | None",
        "Callable[[RecordResult], None] | None",
        asyncio.Event,
    ],
    Awaitable[RecordResult],
]


@dataclass
class _QueueState:
    """Shared per-queue pause flag set by the first auth-barrier failure."""

    paused_by: RecordResult | None = None


async def run_platform_queue(
    tasks: list[UrlTask],
    output_dir: Path,
    *,
    config: TaskConfig,
    scheduler: PlatformTaskScheduler,
    process: ProcessFn,
    relogin_handler: ReloginHandler | None,
    browser_pool: Any,
    on_event: Callable[[TaskEvent], None] | None,
    on_result: Callable[[RecordResult], None] | None,
    cancel_event: asyncio.Event,
    auth_preflight: dict[str, bool],
) -> list[RecordResult]:
    known_block = scheduler.known_auth_block(tasks[0])
    if known_block is not None:
        # A stored EXPIRED marker may be stale; give the preserved state
        # one probe, then offer an interactive re-login before pausing.
        blocked_platform = scheduler.blocked_auth_platform(tasks[0])
        if blocked_platform is not None and await heal_or_relogin(
            relogin_handler, browser_pool, blocked_platform,
            auth_preflight, cancel_event,
        ):
            logger.info(
                "Auth profile for %s restored before crawl; platform not paused.",
                blocked_platform,
            )
            known_block = None
    if known_block is not None:
        return [
            publish_paused_result(scheduler, task, known_block, on_event, on_result)
            for task in tasks
        ]

    state = _QueueState()
    semaphore = asyncio.Semaphore(max(1, config.platform_url_concurrency))

    async def run_one(task: UrlTask) -> RecordResult:
        async with semaphore:
            if cancel_event.is_set():
                return publish_synthetic_result(
                    scheduler.cancelled_result(task),
                    on_event,
                    on_result,
                )
            paused = state.paused_by
            if paused is not None:
                return publish_paused_result(
                    scheduler,
                    task,
                    _pause_message(paused),
                    on_event,
                    on_result,
                )
            result = await _process_with_relogin(task)
            if state.paused_by is None and scheduler.should_pause_after(result):
                state.paused_by = result
            return result

    async def _process_with_relogin(task: UrlTask) -> RecordResult:
        try:
            result = await process(task, output_dir, on_event, on_result, cancel_event)
            if await relogin_after_auth_failure(
                relogin_handler, task, result, cancel_event
            ):
                result = await process(
                    task, output_dir, on_event, on_result, cancel_event
                )
            return result
        except asyncio.CancelledError:
            if not cancel_event.is_set():
                raise
            return publish_synthetic_result(
                scheduler.cancelled_result(task),
                on_event,
                on_result,
            )
        except Exception as error:  # noqa: BLE001 - a hook failure must not sink the queue
            logger.exception("URL wrapper failed for %s", task.original_url)
            return publish_synthetic_result(
                _unexpected_result(task, error),
                on_event,
                on_result,
            )

    return list(await asyncio.gather(*(run_one(task) for task in tasks)))


def publish_synthetic_result(
    result: RecordResult,
    on_event: Callable[[TaskEvent], None] | None,
    on_result: Callable[[RecordResult], None] | None,
) -> RecordResult:
    now = _now()
    result.started_at = now
    result.finished_at = now
    emit_event(result, "finish", result.status.value, on_event)
    if on_result is not None:
        try:
            on_result(result)
        except Exception:
            pass
    return result


def publish_paused_result(
    scheduler: PlatformTaskScheduler,
    task: UrlTask,
    message: str,
    on_event: Callable[[TaskEvent], None] | None,
    on_result: Callable[[RecordResult], None] | None,
) -> RecordResult:
    return publish_synthetic_result(
        scheduler.auth_paused_result(task, message),
        on_event,
        on_result,
    )


def _pause_message(paused_by: RecordResult) -> str:
    return (
        f"同平台记录 #{paused_by.task.evidence_id:03d} 检测到登录或验证屏障；"
        "已暂停该平台剩余 URL，请在“管理平台登录态”中复验后重试。"
    )


def _unexpected_result(task: UrlTask, error: Exception) -> RecordResult:
    return RecordResult(
        task=task,
        status=RecordStatus.FAILED,
        errors=[TaskError("crawl", "UNEXPECTED", str(error), retryable=False)],
    )


def _now() -> datetime:
    return datetime.now(DEFAULT_TIMEZONE)
