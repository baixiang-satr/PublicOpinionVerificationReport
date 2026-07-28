"""Atomic per-record checkpoints for cancellation and export recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from src.domain.models import (
    AssetSet,
    ContentKind,
    ExtractionSource,
    OcrStatus,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    TaskError,
    UrlTask,
)
from src.utils.file_utils import atomic_replace


CHECKPOINT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CheckpointSnapshot:
    job_id: str
    task_fingerprint: str
    records: tuple[RecordResult, ...]


class CheckpointStore:
    def __init__(
        self,
        path: Path,
        *,
        job_id: str,
        tasks: tuple[UrlTask, ...],
    ) -> None:
        self.path = Path(path).resolve()
        self._job_id = job_id
        self._tasks = tasks
        self._records: dict[int, RecordResult] = {}
        self._fingerprint = task_fingerprint(tasks)

    def update(self, record: RecordResult) -> None:
        self._records[record.task.evidence_id] = record
        self.save()

    def update_many(self, records: list[RecordResult]) -> None:
        self._records.update(
            (record.task.evidence_id, record)
            for record in records
        )
        self.save()

    def save(self) -> None:
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "job_id": self._job_id,
            "task_fingerprint": self._fingerprint,
            "input_count": len(self._tasks),
            "records": [
                _record_to_dict(record)
                for record in sorted(
                    self._records.values(),
                    key=lambda item: item.task.evidence_id,
                )
            ],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        atomic_replace(temporary, self.path)

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_tasks: tuple[UrlTask, ...] | None = None,
    ) -> CheckpointSnapshot:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("Unsupported checkpoint schema.")
        fingerprint = str(payload.get("task_fingerprint") or "")
        if (
            expected_tasks is not None
            and fingerprint != task_fingerprint(expected_tasks)
        ):
            raise ValueError("Checkpoint input does not match the requested tasks.")
        records = tuple(
            _record_from_dict(value)
            for value in payload.get("records") or ()
        )
        return CheckpointSnapshot(
            str(payload.get("job_id") or ""),
            fingerprint,
            records,
        )


def task_fingerprint(tasks: tuple[UrlTask, ...]) -> str:
    payload = "\n".join(
        f"{task.evidence_id}\t{task.original_url}\t{task.normalized_url}"
        for task in tasks
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_to_dict(record: RecordResult) -> dict[str, Any]:
    page = record.page
    return {
        "task": {
            "evidence_id": record.task.evidence_id,
            "original_url": record.task.original_url,
            "normalized_url": record.task.normalized_url,
        },
        "status": record.status.value,
        "page": {
            "final_url": page.final_url,
            "title": page.title,
            "content_text": page.content_text,
            "content_summary": page.content_summary,
            "author_name": page.author_name,
            "author_id": page.author_id,
            "author_url": page.author_url,
            "account_uin": page.account_uin,
            "store_name": page.store_name,
            "published_at": _iso(page.published_at),
            "published_at_raw": page.published_at_raw,
            "image_urls": page.image_urls,
            "status_code": page.status_code,
            "redirect_chain": page.redirect_chain,
            "text_type_hint": page.text_type_hint,
            "field_sources": {
                key: value.value
                for key, value in page.field_sources.items()
            },
            "field_confidences": page.field_confidences,
            "summary_truncated": page.summary_truncated,
            "author_id_is_fallback": page.author_id_is_fallback,
            "ocr_text": page.ocr_text,
            "ocr_status": page.ocr_status.value,
            "ocr_image_count": page.ocr_image_count,
            "ocr_text_image_count": page.ocr_text_image_count,
            "content_kind": page.content_kind.value,
            "original_content_chars": page.original_content_chars,
            "exported_content_chars": page.exported_content_chars,
        },
        "route": (
            {
                "sheet_name": record.route.sheet_name,
                "platform_value": record.route.platform_value,
                "text_type": record.route.text_type,
            }
            if record.route is not None
            else None
        ),
        "assets": {
            "page_screenshot": _path(record.assets.page_screenshot),
            "author_screenshot": _path(record.assets.author_screenshot),
        },
        "errors": [
            {
                "stage": error.stage,
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
            }
            for error in record.errors
        ],
        "started_at": _iso(record.started_at),
        "finished_at": _iso(record.finished_at),
        "attempt_count": record.attempt_count,
    }


def _record_from_dict(value: dict[str, Any]) -> RecordResult:
    task_value = value["task"]
    page_value = value.get("page") or {}
    route_value = value.get("route")
    assets_value = value.get("assets") or {}
    page = PageData(
        **{
            key: page_value.get(key)
            for key in (
                "final_url",
                "title",
                "content_text",
                "content_summary",
                "author_name",
                "author_id",
                "author_url",
                "account_uin",
                "store_name",
                "published_at_raw",
                "status_code",
                "text_type_hint",
                "summary_truncated",
                "author_id_is_fallback",
                "ocr_text",
                "ocr_image_count",
                "ocr_text_image_count",
                "original_content_chars",
                "exported_content_chars",
            )
            if key in page_value
        }
    )
    page.published_at = _datetime(page_value.get("published_at"))
    page.image_urls = list(page_value.get("image_urls") or ())
    page.redirect_chain = list(page_value.get("redirect_chain") or ())
    page.field_sources = {
        key: ExtractionSource(source)
        for key, source in (page_value.get("field_sources") or {}).items()
    }
    page.field_confidences = {
        str(key): float(confidence)
        for key, confidence in (
            page_value.get("field_confidences") or {}
        ).items()
    }
    page.ocr_status = OcrStatus(
        page_value.get("ocr_status") or OcrStatus.NOT_RUN
    )
    page.content_kind = ContentKind(
        page_value.get("content_kind") or ContentKind.UNKNOWN
    )
    record = RecordResult(
        task=UrlTask(
            int(task_value["evidence_id"]),
            str(task_value["original_url"]),
            str(task_value["normalized_url"]),
        ),
        status=RecordStatus(value["status"]),
        page=page,
        route=(
            RouteDecision(
                str(route_value["sheet_name"]),
                str(route_value["platform_value"]),
                str(route_value["text_type"]),
            )
            if route_value
            else None
        ),
        assets=AssetSet(
            _optional_path(assets_value.get("page_screenshot")),
            _optional_path(assets_value.get("author_screenshot")),
        ),
        errors=[
            TaskError(
                str(error["stage"]),
                str(error["code"]),
                str(error["message"]),
                bool(error.get("retryable")),
            )
            for error in value.get("errors") or ()
        ],
        started_at=_datetime(value.get("started_at")),
        finished_at=_datetime(value.get("finished_at")),
        attempt_count=int(value.get("attempt_count") or 0),
    )
    return record


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _path(value: Path | None) -> str | None:
    return str(Path(value).resolve()) if value is not None else None


def _optional_path(value: Any) -> Path | None:
    return Path(str(value)) if value else None
