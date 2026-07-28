"""Immutable messages shared by the task runner and desktop UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.domain.models import RecordResult, TaskEvent, UrlTask


@dataclass(frozen=True)
class JobRequest:
    input_path: Path | None = None
    tasks: tuple[UrlTask, ...] = ()
    retained_records: tuple[RecordResult, ...] = ()
    sheet_name: str | None = None
    job_id: str | None = None
    label: str = "批量抓取"

    def __post_init__(self) -> None:
        if (self.input_path is None) == (not self.tasks):
            raise ValueError("JobRequest requires either input_path or tasks.")


@dataclass(frozen=True)
class JobSummary:
    job_id: str
    label: str
    total: int
    rejected_count: int


@dataclass(frozen=True)
class ProgressSnapshot:
    completed: int
    total: int
    ready: int
    needs_review: int
    failed: int
    cancelled: int
    current_url: str = ""
    stage: str = "准备中"

    @property
    def percent(self) -> int:
        return 0 if self.total == 0 else round(self.completed * 100 / self.total)


@dataclass(frozen=True)
class LogEvent:
    timestamp: datetime
    level: str
    message: str
    evidence_id: int | None = None


@dataclass(frozen=True)
class JobResult:
    job_id: str
    label: str
    records: tuple[RecordResult, ...]
    rejected_count: int
    job_dir: Path
    archive_path: Path | None = None
    cancelled: bool = False
    quality_report_path: Path | None = None
    quality_summary_path: Path | None = None
    manual_entry_path: Path | None = None

    @property
    def retryable_tasks(self) -> tuple[UrlTask, ...]:
        retryable_statuses = {"failed", "needs_review", "cancelled"}
        return tuple(
            record.task
            for record in self.records
            if record.status.value in retryable_statuses
        )


@dataclass
class RunnerCallbacks:
    started: Callable[[JobSummary], None] | None = None
    task_event: Callable[[TaskEvent], None] | None = None
    record_updated: Callable[[RecordResult], None] | None = None
    progress: Callable[[ProgressSnapshot], None] | None = None
    log: Callable[[LogEvent], None] | None = None
