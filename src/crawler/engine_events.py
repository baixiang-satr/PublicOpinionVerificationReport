"""TaskEvent emission helper for the crawl engine (split for the line cap)."""

from __future__ import annotations

from collections.abc import Callable

from src.domain.models import RecordResult, TaskEvent


def emit_event(
    result: RecordResult,
    stage: str,
    message: str,
    callback: Callable[[TaskEvent], None] | None,
) -> None:
    if callback is None:
        return
    try:
        callback(TaskEvent(result.task.evidence_id, result.status, stage, message))
    except Exception:
        pass
