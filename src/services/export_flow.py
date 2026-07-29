"""Row-building and author-evidence staging flow extracted from TaskRunner."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.crawler.platform_router import PlatformRouter
from src.domain.models import (
    RecordResult,
    RecordStatus,
    TaskError,
    TemplateRow,
)
from src.export.row_mapper import TemplateRowMapper, TemplateRowMappingError
from src.export.staging_assets import audit_staged_author_assets
from src.screenshot.author_evidence import (
    AuthorEvidenceDecision,
    read_decision,
    write_decision,
)


def build_export_rows(
    records: list[RecordResult],
    row_mapper: TemplateRowMapper,
    platform_router: PlatformRouter,
    record_updated: Callable[[RecordResult], None] | None = None,
) -> list[TemplateRow]:
    """Map complete runtime records to template rows, preserving blanks."""

    rows: list[TemplateRow] = []
    for record in records:
        if record.status in {
            RecordStatus.PENDING,
            RecordStatus.RUNNING,
            RecordStatus.CANCELLED,
        }:
            continue
        if record.route is None:
            route_url = record.task.normalized_url
            record.route = platform_router.route(route_url, record.page)
        if record.route is None:
            record.errors.append(
                TaskError(
                    "export_validation",
                    "ROW_ROUTE_UNAVAILABLE",
                    "URL 未匹配固定模板平台，无法保留对应工作表行。",
                    retryable=False,
                )
            )
            if record.status == RecordStatus.ASSETS_READY:
                record.status = RecordStatus.NEEDS_REVIEW
            _notify(record_updated, record)
            continue
        was_assets_ready = record.status == RecordStatus.ASSETS_READY
        if was_assets_ready:
            record.status = RecordStatus.READY_FOR_EXPORT
        try:
            row = row_mapper.map(record)
            rows.append(row)
        except TemplateRowMappingError as error:
            if was_assets_ready:
                record.status = RecordStatus.NEEDS_REVIEW
            record.errors.append(
                TaskError("export_validation", "ROW_MAPPING_FAILED", str(error))
            )
        _notify(record_updated, record)
    return rows


def audit_and_archive_author_evidence(
    template_dir: Path,
    rows: list[TemplateRow],
    job_dir: Path,
) -> tuple[list[TemplateRow], list[AuthorEvidenceDecision], list[dict[str, Any]]]:
    """Audit staged author screenshots and archive their decisions.

    Returns the updated rows (rejected attachments stripped), every persisted
    :class:`AuthorEvidenceDecision` found in staging, and the audit removal
    entries.  Decision JSONs are copied into ``job_dir/author_decisions`` so
    the audit trail survives staging cleanup and never enters the ZIP.
    """

    decisions: list[AuthorEvidenceDecision] = []
    for sidecar in sorted(Path(template_dir).glob("*主页.decision.json")):
        decision = read_decision(sidecar)
        if decision is not None:
            decisions.append(decision)

    updated_rows, entries = audit_staged_author_assets(template_dir, rows)

    if decisions:
        destination = Path(job_dir) / "author_decisions"
        destination.mkdir(parents=True, exist_ok=True)
        for decision in decisions:
            try:
                write_decision(decision, destination)
            except Exception:
                pass
    return updated_rows, decisions, entries


def _notify(
    callback: Callable[[RecordResult], None] | None,
    record: RecordResult,
) -> None:
    if callback is None:
        return
    try:
        callback(record)
    except Exception:
        pass
