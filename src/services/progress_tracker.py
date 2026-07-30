"""Progress tracking helpers for TaskRunner (kept apart for the line limit)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.domain.models import RecordResult, RecordStatus, TaskEvent, UrlTask
from src.services.models import LogEvent, ProgressSnapshot, RunnerCallbacks
from src.utils.time_utils import DEFAULT_TIMEZONE


def _call(callback: Callable[[Any], None] | None, value: Any) -> None:
    if callback is None:
        return
    try:
        callback(value)
    except Exception:
        pass


def _event_stage_text(event: TaskEvent) -> str:
    return {
        "start": "正在抓取网页",
        "retry": "正在重试",
        "finish": "已完成一条记录",
    }.get(event.stage, event.message)


class _ProgressTracker:
    def __init__(self, tasks: tuple[UrlTask, ...], callbacks: RunnerCallbacks) -> None:
        self._tasks = {task.evidence_id: task for task in tasks}
        self._callbacks = callbacks
        self._records: dict[int, RecordResult] = {}
        self._finished: dict[int, RecordStatus] = {}
        self._current_url = ""

    def on_task_event(self, event: TaskEvent) -> None:
        _call(self._callbacks.task_event, event)
        task = self._tasks.get(event.evidence_id)
        self._current_url = task.original_url if task else ""
        if event.stage == "finish":
            self._finished[event.evidence_id] = event.status
        level = "WARNING" if event.stage == "retry" else "INFO"
        _call(
            self._callbacks.log,
            LogEvent(
                datetime.now(DEFAULT_TIMEZONE),
                level,
                f"#{event.evidence_id:03d} {event.message}",
                event.evidence_id,
            ),
        )
        self.publish(stage=_event_stage_text(event))

    def on_record(self, record: RecordResult) -> None:
        self._records[record.task.evidence_id] = record
        _call(self._callbacks.record_updated, record)
        self.publish(stage="已完成一条记录")

    def publish(
        self,
        records: list[RecordResult] | None = None,
        *,
        stage: str,
    ) -> None:
        source = records if records is not None else list(self._records.values())
        statuses = [record.status for record in source]
        snapshot = ProgressSnapshot(
            completed=len(self._finished) if records is None else len(self._tasks),
            total=len(self._tasks),
            ready=sum(
                status in {RecordStatus.ASSETS_READY, RecordStatus.READY_FOR_EXPORT, RecordStatus.EXPORTED}
                for status in statuses
            ),
            needs_review=statuses.count(RecordStatus.NEEDS_REVIEW),
            failed=statuses.count(RecordStatus.FAILED),
            cancelled=statuses.count(RecordStatus.CANCELLED),
            current_url=self._current_url,
            stage=stage,
        )
        _call(self._callbacks.progress, snapshot)
