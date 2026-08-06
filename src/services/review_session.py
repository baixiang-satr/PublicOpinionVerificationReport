"""Session state for the review workspace (采集与补录).

This module is the testable, Qt-free core behind the review UI: it loads a
job's checkpoint records plus persisted manual overrides, computes the
*effective* value of every editable template field (manual wins), and tracks
which required fields are still missing so the operator can jump straight to
the next incomplete record.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from src.domain.models import RecordResult, RecordStatus, RouteDecision, UrlTask
from src.domain.overrides import OVERRIDEABLE_FIELDS, ManualOverride
from src.domain.template_schema import SHEET_LAYOUTS, SheetLayout
from src.services import job_records
from src.services import recovery_mirror
from src.services.checkpoint_store import CheckpointStore
from src.services.manual_assets import MANUAL_ASSETS_DIR_NAME
from src.services.override_store import ManualOverrideStore
from src.services.record_disposal import delete_record_artifacts
from src.services.review_models import ReviewFieldView, ReviewRecordSummary

#: Field keys shown in the editor, in canonical template order.
_EDITOR_FIELDS: tuple[str, ...] = OVERRIDEABLE_FIELDS

_MULTILINE_FIELDS = frozenset({"content"})

_FALLBACK_LABELS = {
    "title": "标题",
    "content": "信息内容",
    "author_name": "昵称",
    "author_id": "用户账号",
    "account_uin": "UIN",
    "store_name": "店铺名称",
    "published_at": "发布时间",
    "text_type": "文本类型",
    "platform": "发布平台",
}


class ReviewSession:
    """Holds one job's records plus its manual override store."""

    def __init__(
        self,
        job_dir: Path,
        records: list[RecordResult],
        store: ManualOverrideStore,
    ) -> None:
        self.job_dir = Path(job_dir)
        self._records = {record.task.evidence_id: record for record in records}
        self.store = store

    @classmethod
    def from_job_dir(cls, job_dir: Path) -> "ReviewSession":
        job_dir = Path(job_dir)
        snapshot = CheckpointStore.load(job_dir / "job_checkpoint.json")
        store = ManualOverrideStore(job_dir).load()
        return cls(job_dir, list(snapshot.records), store)

    @classmethod
    def from_records(
        cls,
        job_dir: Path,
        records: list[RecordResult],
    ) -> "ReviewSession":
        store = ManualOverrideStore(job_dir).load()
        return cls(job_dir, records, store)

    # ── queries ──
    def evidence_ids(self) -> list[int]:
        return sorted(self._records)

    def records(self) -> list[RecordResult]:
        """全部记录（按证据号排序），供断点重建/导出兜底使用。"""

        return self._ordered_records()

    def get_record(self, evidence_id: int) -> RecordResult:
        return self._records[evidence_id]

    def get_override(self, evidence_id: int) -> ManualOverride | None:
        return self.store.get(evidence_id)

    def manual_assets_dir(self) -> Path:
        return self.job_dir / MANUAL_ASSETS_DIR_NAME

    def layout_for(self, record: RecordResult) -> SheetLayout | None:
        if record.route is None:
            return None
        return SHEET_LAYOUTS.get(record.route.sheet_name)

    def summaries(self) -> list[ReviewRecordSummary]:
        return [self.summary_for(record) for record in self._ordered_records()]

    def summary_for(self, record: RecordResult) -> ReviewRecordSummary:
        override = self.store.get(record.task.evidence_id)
        return ReviewRecordSummary(
            evidence_id=record.task.evidence_id,
            original_url=record.task.original_url,
            final_url=record.page.final_url,
            sheet_name=record.route.sheet_name if record.route else "",
            platform_value=record.route.platform_value if record.route else "",
            status=record.status,
            missing_labels=self.missing_labels(record, override),
            has_override=override is not None and not override.is_empty(),
        )

    def field_views(self, evidence_id: int) -> list[ReviewFieldView]:
        record = self._records[evidence_id]
        override = self.store.get(evidence_id)
        layout = self.layout_for(record)
        views: list[ReviewFieldView] = []
        for field in self._fields_for_layout(layout):
            value, source = self._effective_value(record, override, field)
            required = self._is_required(layout, field)
            views.append(
                ReviewFieldView(
                    field=field,
                    label=self._label_for(layout, field),
                    value=value,
                    source=source,
                    required=required,
                    missing=required and not value.strip(),
                    multiline=field in _MULTILINE_FIELDS,
                    choices=self._choices_for(layout, field),
                )
            )
        return views

    def missing_labels(
        self,
        record: RecordResult,
        override: ManualOverride | None,
    ) -> tuple[str, ...]:
        layout = self.layout_for(record)
        swapped = layout is not None and layout.homepage_screenshot_primary
        missing: list[str] = []
        for field in self._fields_for_layout(layout):
            if not self._is_required(layout, field):
                continue
            value, _source = self._effective_value(record, override, field)
            if not value.strip():
                missing.append(self._label_for(layout, field))
        if self._screenshot_required(layout) and not self.primary_screenshot_name(record):
            missing.append("主页截图" if swapped else "截图")
        if self._secondary_screenshot_missing(record, override, layout):
            missing.append("内容页截图" if swapped else "主页截图")
        return tuple(missing)

    def _secondary_screenshot_missing(
        self,
        record: RecordResult,
        override: ManualOverride | None,
        layout: SheetLayout | None,
    ) -> bool:
        """带链接的记录必须有两张截图；无 URL 的手工行不强制。

        常规表第二张=个人主页截图（经附件列交付，有效附件为空即缺失）；对调表
        第二张=内容页截图（内容页槽位为空即缺失）。
        """

        if layout is None or layout.attachment_column is None:
            return False
        if not record.task.original_url.strip():
            return False
        if layout.homepage_screenshot_primary:
            if override is not None and override.primary_screenshot_name:
                return False
            return record.assets.page_screenshot is None
        if override is not None and override.author_screenshot_name:
            return False
        if override is not None and override.attachment_names:
            return False
        return not record.assets.attachment_paths()

    def content_screenshot_name(self, record: RecordResult) -> str | None:
        """Effective content-page (内容页) screenshot name; manual wins."""
        override = self.store.get(record.task.evidence_id)
        if override is not None and override.primary_screenshot_name:
            return override.primary_screenshot_name
        if record.assets.page_screenshot is not None:
            return record.assets.page_screenshot.name
        return None

    def primary_screenshot_name(self, record: RecordResult) -> str | None:
        """写入主截图列的截图名：对调表为个人主页截图，其余为内容页截图。"""
        layout = self.layout_for(record)
        if layout is not None and layout.homepage_screenshot_primary:
            return self.author_screenshot_name(record)
        return self.content_screenshot_name(record)

    def author_screenshot_name(self, record: RecordResult) -> str | None:
        """Effective author-home (个人页) screenshot name; manual wins."""
        override = self.store.get(record.task.evidence_id)
        if override is not None and override.author_screenshot_name:
            return override.author_screenshot_name
        if record.assets.author_screenshot is not None:
            return record.assets.author_screenshot.name
        return None

    def content_screenshot_path(self, record: RecordResult) -> Path | None:
        """Best-effort existing file for the content-page thumbnail preview."""
        override = self.store.get(record.task.evidence_id)
        name = override.primary_screenshot_name if override is not None else None
        return self._slot_path(name, record.assets.page_screenshot)

    def primary_screenshot_path(self, record: RecordResult) -> Path | None:
        """主截图列对应文件的预览路径；对调表为个人主页截图。"""
        layout = self.layout_for(record)
        if layout is not None and layout.homepage_screenshot_primary:
            return self.author_screenshot_path(record)
        return self.content_screenshot_path(record)

    def author_screenshot_path(self, record: RecordResult) -> Path | None:
        """Best-effort existing file for the author-home screenshot preview."""
        override = self.store.get(record.task.evidence_id)
        name = override.author_screenshot_name if override is not None else None
        return self._slot_path(name, record.assets.author_screenshot)

    def _slot_path(self, override_name: str | None, crawled: Path | None) -> Path | None:
        """Resolve one screenshot slot: manual-assets name wins over the crawled file.

        任务目录文件可能被外部清理；主路径缺失时回退到恢复镜像。
        """
        if override_name:
            candidate = self.manual_assets_dir() / override_name
            if candidate.exists():
                return candidate
            return recovery_mirror.mirrored_asset(self.job_dir.name, override_name)
        if crawled is not None and Path(crawled).exists():
            return Path(crawled)
        if crawled is not None:
            return recovery_mirror.mirrored_asset(self.job_dir.name, Path(crawled).name)
        return None

    def completion_counts(self) -> tuple[int, int]:
        summaries = self.summaries()
        done = sum(1 for summary in summaries if not summary.needs_attention)
        return done, len(summaries)

    def next_attention_id(
        self,
        current_id: int,
        *,
        backwards: bool = False,
    ) -> int | None:
        ids = self.evidence_ids()
        if not ids:
            return None
        ordered = list(reversed(ids)) if backwards else ids
        if current_id in ordered:
            start = ordered.index(current_id)
            rotated = [*ordered[start + 1 :], *ordered[:start]]
        else:
            rotated = ordered
        for evidence_id in rotated:
            summary = self.summary_for(self._records[evidence_id])
            if summary.needs_attention:
                return evidence_id
        return None

    # ── mutations (persist immediately) ──
    def set_field(self, evidence_id: int, field: str, value: str) -> None:
        self.store.set_field(evidence_id, field, value)

    def set_note(self, evidence_id: int, note: str) -> None:
        self.store.set_note(evidence_id, note)

    def set_primary_screenshot(self, evidence_id: int, name: str | None) -> None:
        self.store.set_primary_screenshot(evidence_id, name)

    def set_author_screenshot(self, evidence_id: int, name: str | None) -> None:
        self.store.set_author_screenshot(evidence_id, name)

    def set_attachments(self, evidence_id: int, names: list[str]) -> None:
        self.store.set_attachments(evidence_id, names)

    def set_text_type_many(self, evidence_ids: list[int], text_type: str) -> list[int]:
        """Batch-set text_type where the sheet allows it; return skipped ids."""

        skipped: list[int] = []
        for evidence_id in evidence_ids:
            record = self._records.get(evidence_id)
            layout = self.layout_for(record) if record is not None else None
            if layout is None:
                skipped.append(evidence_id)
                continue
            column = layout.field_columns.get("text_type")
            allowed = layout.validation_values.get(column or "", ())
            if text_type not in allowed:
                skipped.append(evidence_id)
                continue
            self.store.set_field(evidence_id, "text_type", text_type)
        return skipped

    def copy_empty_fields_from(self, source_id: int, target_id: int) -> list[str]:
        """Copy the source record's effective values into the target's empty
        fields (never overwriting existing target content); return copied
        field keys."""

        source = self._records.get(source_id)
        target = self._records.get(target_id)
        if source is None or target is None or source_id == target_id:
            return []
        source_override = self.store.get(source_id)
        target_override = self.store.get(target_id)
        copied: list[str] = []
        for field in _EDITOR_FIELDS:
            _target_value, target_source = self._effective_value(
                target, target_override, field
            )
            if target_source != "empty":
                continue
            value, _source_kind = self._effective_value(
                source, source_override, field
            )
            if not value.strip():
                continue
            self.store.set_field(target_id, field, value)
            copied.append(field)
        return copied

    def sheet_completion(self) -> dict[str, tuple[int, int]]:
        """sheet name -> (records not needing attention, total records)."""

        completion: dict[str, list[int]] = {}
        for summary in self.summaries():
            bucket = completion.setdefault(summary.sheet_name or "未匹配", [0, 0])
            bucket[1] += 1
            if not summary.needs_attention:
                bucket[0] += 1
        return {name: (done, total) for name, (done, total) in completion.items()}

    def previous_id(self, evidence_id: int) -> int | None:
        ids = self.evidence_ids()
        if evidence_id in ids and ids.index(evidence_id) > 0:
            return ids[ids.index(evidence_id) - 1]
        return None

    # ── manual rows (群聊/朋友圈) 与记录删除 ──
    @staticmethod
    def is_manual_row(record: RecordResult) -> bool:
        """Rows with an empty original URL only exist via import or manual add."""

        return record.route is not None and not record.task.original_url.strip()

    def add_manual_record(self, sheet_name: str) -> RecordResult:
        """Append a blank manual row to ``sheet_name`` and persist it."""

        layout = SHEET_LAYOUTS[sheet_name]
        evidence_id = (max(self._records) + 1) if self._records else 1
        record = RecordResult(
            task=UrlTask(evidence_id, "", ""),
            status=RecordStatus.NEEDS_REVIEW,
            route=RouteDecision(
                sheet_name=layout.name,
                platform_value="",
                text_type="正文",
            ),
        )
        self._records[evidence_id] = record
        if job_records.checkpoint_exists(self.job_dir):
            job_records.append_record(self.job_dir, record)
        return record

    def remove_record(self, evidence_id: int) -> bool:
        """删除任意记录（含带 URL 的抓取行），联动清理截图/覆盖/断点。"""

        record = self._records.get(evidence_id)
        if record is None:
            return False
        delete_record_artifacts(
            self.job_dir,
            record,
            self.store.get(evidence_id),
        )
        del self._records[evidence_id]
        self.store.remove(evidence_id)
        if job_records.checkpoint_exists(self.job_dir):
            job_records.remove_record(self.job_dir, evidence_id)
        return True

    # ── internals ──
    def _ordered_records(self) -> list[RecordResult]:
        return [self._records[evidence_id] for evidence_id in self.evidence_ids()]

    def _fields_for_layout(self, layout: SheetLayout | None) -> tuple[str, ...]:
        if layout is None:
            return _EDITOR_FIELDS
        ordered = sorted(
            layout.field_columns.items(),
            key=lambda item: layout.column_number(item[1]),
        )
        return tuple(
            field for field, _column in ordered if field in _EDITOR_FIELDS
        )

    def _effective_value(
        self,
        record: RecordResult,
        override: ManualOverride | None,
        field: str,
    ) -> tuple[str, str]:
        if override is not None:
            manual = (override.values.get(field) or "").strip()
            if manual:
                return manual, "manual"
        page = record.page
        layout = self.layout_for(record)
        crawled = ""
        if field == "content":
            crawled = page.content_text or page.content_summary or ""
        elif field == "published_at":
            # 解析失败时不再回显原始串（如抖音异常的 245000），
            # 发布时间宁可留空待补录；raw 保留在 checkpoint 供诊断。
            crawled = (
                page.published_at.strftime("%Y-%m-%d %H:%M:%S")
                if page.published_at
                else ""
            )
        elif field == "text_type":
            crawled = record.route.text_type if record.route else page.text_type_hint
        elif field == "platform":
            crawled = record.route.platform_value if record.route else ""
        elif field in {
            "title",
            "author_name",
            "author_id",
            "account_uin",
            "store_name",
        }:
            crawled = getattr(page, field) or ""
        if layout is not None and "account_uin" in layout.field_columns:
            # 公众号表：微信号(必填)列直接交付公众号昵称；UIN 不采集，留空。
            if field == "author_id":
                crawled = page.author_name or ""
            elif field == "account_uin":
                crawled = ""
        return crawled, "crawled" if crawled.strip() else "empty"

    def _is_required(self, layout: SheetLayout | None, field: str) -> bool:
        if layout is None:
            return field in {"title", "content", "author_name"}
        column = layout.field_columns.get(field)
        return bool(column) and column in layout.required_columns

    def _screenshot_required(self, layout: SheetLayout | None) -> bool:
        if layout is None or layout.primary_screenshot_column is None:
            return True
        return layout.primary_screenshot_column in layout.required_columns

    def _label_for(self, layout: SheetLayout | None, field: str) -> str:
        if layout is None:
            return _FALLBACK_LABELS.get(field, field)
        column = layout.field_columns.get(field)
        if column:
            index = layout.column_number(column) - 1
            if 0 <= index < len(layout.headers):
                header = layout.headers[index]
                return re.split(r"[（(]", header, maxsplit=1)[0].strip() or header
        return _FALLBACK_LABELS.get(field, field)

    def _choices_for(
        self,
        layout: SheetLayout | None,
        field: str,
    ) -> tuple[str, ...]:
        if layout is None or field not in {"text_type", "platform"}:
            return ()
        column = layout.field_columns.get(field)
        if not column:
            return ()
        return tuple(layout.validation_values.get(column, ()))
