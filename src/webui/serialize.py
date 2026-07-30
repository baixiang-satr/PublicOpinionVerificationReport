"""webui 载荷序列化：把 ReviewSession / 运行事件转成 JS 侧可消费的 dict。

字段命名与 web/src/types.ts 一一对应，改动必须双端同步。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.auth.models import AuthProfile, AuthStatus
from src.domain.models import RecordResult, RecordStatus
from src.domain.overrides import OVERRIDEABLE_FIELDS
from src.domain.template_schema import SHEET_LAYOUTS, SHEET_ORDER, SheetLayout
from src.services import sheet_display
from src.services.models import JobResult, LogEvent, ProgressSnapshot
from src.services.review_session import ReviewSession

_STATUS_TEXT = {
    RecordStatus.EXPORTED: "成功",
    RecordStatus.NEEDS_REVIEW: "待补录",
    RecordStatus.FAILED: "失败",
    RecordStatus.CANCELLED: "已取消",
}

_AUTH_STATUS_TEXT = {
    AuthStatus.UNKNOWN: "未检查",
    AuthStatus.PROBING: "正在验证",
    AuthStatus.GUEST_OK: "游客可访问",
    AuthStatus.AUTH_REQUIRED: "需要登录",
    AuthStatus.CHALLENGE: "需要人工验证",
    AuthStatus.WAITING_USER: "等待人工操作",
    AuthStatus.VALIDATING: "正在复验",
    AuthStatus.VALID: "登录态有效",
    AuthStatus.EXPIRED: "登录态已过期",
    AuthStatus.INVALID_URL: "URL失效/页面为空",
    AuthStatus.ACCESS_BLOCKED: "访问受限",
    AuthStatus.ERROR: "验证失败",
}

_AUTH_TONE = {
    AuthStatus.VALID: "ok",
    AuthStatus.GUEST_OK: "ok",
    AuthStatus.PROBING: "warn",
    AuthStatus.VALIDATING: "warn",
    AuthStatus.WAITING_USER: "warn",
    AuthStatus.AUTH_REQUIRED: "warn",
    AuthStatus.CHALLENGE: "warn",
    AuthStatus.EXPIRED: "warn",
    AuthStatus.ERROR: "err",
    AuthStatus.ACCESS_BLOCKED: "err",
    AuthStatus.INVALID_URL: "err",
    AuthStatus.UNKNOWN: "muted",
}


def progress_payload(snapshot: ProgressSnapshot) -> dict:
    return {
        "completed": snapshot.completed,
        "total": snapshot.total,
        "ready": snapshot.ready,
        "needs_review": snapshot.needs_review,
        "failed": snapshot.failed,
        "cancelled": snapshot.cancelled,
        "current_url": snapshot.current_url,
        "stage": snapshot.stage,
        "percent": snapshot.percent,
    }


def log_payload(event: LogEvent) -> dict:
    return {
        "time": event.timestamp.strftime("%H:%M:%S"),
        "level": event.level,
        "message": event.message,
        "evidence_id": event.evidence_id,
    }


def finished_payload(result: JobResult) -> dict:
    counts = {status: 0 for status in ("exported", "needs_review", "failed", "cancelled")}
    for record in result.records:
        counts[record.status.value] = counts.get(record.status.value, 0) + 1
    return {
        "job_id": result.job_id,
        "label": result.label,
        "archive_path": str(result.archive_path) if result.archive_path else None,
        "cancelled": result.cancelled,
        "ready": counts.get("exported", 0),
        "needs_review": counts.get("needs_review", 0),
        "failed": counts.get("failed", 0),
        "cancelled_count": counts.get("cancelled", 0),
        "retryable": len(result.retryable_tasks),
    }


def session_overview(session: ReviewSession | None) -> dict | None:
    if session is None:
        return None
    done, total = session.completion_counts()
    completion = session.sheet_completion()
    sheets = [
        {"name": name, "done": completion.get(name, (0, 0))[0], "total": completion.get(name, (0, 0))[1]}
        for name in SHEET_ORDER
    ]
    return {"job_dir": str(session.job_dir), "done": done, "total": total, "sheets": sheets}


def _column_kind(layout: SheetLayout, field: str | None, letter: str) -> str:
    if field == "url":
        return "url"
    if layout.primary_screenshot_column == letter:
        return "screenshot"
    if layout.attachment_column == letter:
        return "attachment"
    return "text"


def sheet_columns(layout: SheetLayout) -> list[dict]:
    letters = [chr(ord("A") + index) for index in range(layout.column_count)]
    letter_to_field = {letter: field for field, letter in layout.field_columns.items()}
    columns: list[dict] = []
    for letter in letters:
        header_index = ord(letter) - ord("A")
        field = letter_to_field.get(letter)
        editable = field in OVERRIDEABLE_FIELDS if field else False
        columns.append(
            {
                "key": letter,
                "header": layout.headers[header_index] if header_index < len(layout.headers) else letter,
                "field": field,
                "editable": bool(editable),
                "required": letter in layout.required_columns,
                "multiline": field == "content",
                "choices": list(layout.validation_values.get(letter, ())),
                "kind": _column_kind(layout, field, letter),
            }
        )
    return columns


def record_row(session: ReviewSession, record: RecordResult, layout: SheetLayout) -> dict:
    summary = session.summary_for(record)
    return {
        "eid": record.task.evidence_id,
        "cells": sheet_display.row_values(session, record, layout),
        "status": record.status.value,
        "status_text": _STATUS_TEXT.get(record.status, record.status.value),
        "attention": summary.needs_attention,
        "missing": list(summary.missing_labels),
        "manual": ReviewSession.is_manual_row(record),
        "url": record.task.original_url,
        "final_url": record.page.final_url or "",
    }


def sheet_payload(session: ReviewSession) -> list[dict]:
    by_sheet: dict[str, list[RecordResult]] = {name: [] for name in SHEET_ORDER}
    for evidence_id in session.evidence_ids():
        record = session.get_record(evidence_id)
        if record.route and record.route.sheet_name in by_sheet:
            by_sheet[record.route.sheet_name].append(record)
    payload: list[dict] = []
    for name in SHEET_ORDER:
        layout = SHEET_LAYOUTS[name]
        records = by_sheet[name]
        payload.append(
            {
                "name": name,
                "columns": sheet_columns(layout),
                "rows": [record_row(session, record, layout) for record in records],
                "manual_row_allowed": "url" not in layout.field_columns,
            }
        )
    return payload


def row_delta(session: ReviewSession, evidence_id: int) -> dict:
    record = session.get_record(evidence_id)
    summary = session.summary_for(record)
    return {
        "missing": list(summary.missing_labels),
        "attention": summary.needs_attention,
        "status_text": _STATUS_TEXT.get(record.status, record.status.value),
    }


def auth_platform_payload(
    platform_key: str,
    display_name: str,
    status: AuthStatus,
    message: str,
    account: str = "",
) -> dict:
    return {
        "key": platform_key,
        "name": display_name,
        "status": status.value,
        "status_text": _AUTH_STATUS_TEXT.get(status, status.value),
        "tone": _AUTH_TONE.get(status, "muted"),
        "message": message,
        "account": account,
    }


def auth_profile_payload(display_name: str, profile: AuthProfile) -> dict:
    message = profile.last_message or "尚未验证过该平台。"
    return auth_platform_payload(
        profile.platform_key,
        display_name,
        profile.status,
        message,
        profile.masked_phone or "",
    )


def history_job_payload(path: Path, record_count: int) -> dict:
    return {
        "path": str(path),
        "name": path.name,
        "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "records": record_count,
    }
