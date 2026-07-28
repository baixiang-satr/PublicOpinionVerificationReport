"""Coordinate input, crawling, fixed-template export, cancellation and progress."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from src.config.settings import AppConfig, TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import (
    RecordResult,
    RecordStatus,
    TaskError,
    TaskEvent,
    TemplateRow,
    UrlTask,
)
from src.domain.template_schema import get_sheet_layout
from src.export.excel_writer import ExcelAutomationUnavailable, ExcelTemplateWriter, TemplateIntegrityError
from src.export.package_validator import validate_template_assets
from src.export.packager import create_template_archive
from src.export.row_mapper import TemplateRowMapper, TemplateRowMappingError
from src.export.template_manager import PreparedTemplate, TemplateManager
from src.input.reader import InputReadError, read_url_input
from src.services.models import (
    JobRequest,
    JobResult,
    JobSummary,
    LogEvent,
    ProgressSnapshot,
    RunnerCallbacks,
)
from src.utils.time_utils import DEFAULT_TIMEZONE
from src.utils.file_utils import require_safe_file_name
from src.screenshot.browser import BrowserUnavailableError

# ⚠️ 临时功能：爬取运行报告，项目完成后需删除
from src.tools.crawl_tracker import append_run_report  # noqa: F811


class TaskRunnerError(RuntimeError):
    """A fatal job-level failure suitable for display in the desktop UI."""


class TaskRunner:
    def __init__(
        self,
        config: AppConfig,
        *,
        template_manager: TemplateManager | None = None,
        engine_factory: Callable[[TaskConfig], CrawlEngine] | None = None,
        row_mapper: TemplateRowMapper | None = None,
        excel_writer: ExcelTemplateWriter | None = None,
        asset_validator: Callable[..., Any] = validate_template_assets,
        packager: Callable[..., Path] = create_template_archive,
    ) -> None:
        self._config = config
        self._template_manager = template_manager or TemplateManager(config.template)
        self._engine_factory = engine_factory or CrawlEngine
        self._row_mapper = row_mapper or TemplateRowMapper()
        self._excel_writer = excel_writer or ExcelTemplateWriter(config.template.workbook_name)
        self._asset_validator = asset_validator
        self._packager = packager

    async def run(
        self,
        request: JobRequest,
        callbacks: RunnerCallbacks | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> JobResult:
        callbacks = callbacks or RunnerCallbacks()
        cancellation = cancel_event or asyncio.Event()
        prepared: PreparedTemplate | None = None
        try:
            tasks, rejected_count = await self._resolve_tasks(request)
            if not tasks:
                raise TaskRunnerError("输入文件中没有可处理的 HTTP(S) URL。")
            job_id = request.job_id or _new_job_id()
            _call(callbacks.started, JobSummary(job_id, request.label, len(tasks), rejected_count))
            self._log(callbacks, "INFO", f"已读取 {len(tasks)} 条有效 URL，开始准备任务目录。")
            prepared = await asyncio.to_thread(self._template_manager.prepare, job_id)
            if cancellation.is_set():
                return self._cancelled_result(request, prepared, (), rejected_count)

            tracker = _ProgressTracker(tasks, callbacks)
            retained = await asyncio.to_thread(
                _copy_retained_records,
                request.retained_records,
                prepared.template_dir,
            )
            for record in retained:
                _call(callbacks.record_updated, record)
            tracker.publish(stage="正在启动浏览器")
            engine = self._engine_factory(self._config.task)
            records = await engine.run(
                list(tasks),
                prepared.template_dir,
                on_event=tracker.on_task_event,
                on_result=tracker.on_record,
                cancel_event=cancellation,
            )
            records = [*retained, *records]
            records.sort(key=lambda item: item.task.evidence_id)
            if cancellation.is_set() or any(
                record.status == RecordStatus.CANCELLED for record in records
            ):
                self._template_manager.assert_source_unchanged(prepared)
                tracker.publish(records, stage="任务已取消")
                self._log(callbacks, "WARNING", "任务已取消，已完成结果仅供查看，不生成压缩包。")
                self._write_crawl_report(records, prepared.job_id, request.label, rejected_count)
                return self._cancelled_result(request, prepared, records, rejected_count)

            rows = self._build_rows(records, callbacks)
            if not rows:
                self._template_manager.assert_source_unchanged(prepared)
                tracker.publish(records, stage="没有可导出的记录")
                self._log(
                    callbacks,
                    "WARNING",
                    "没有取得可审计内容和主截图，因此未生成空的 template.zip。",
                )
                self._write_crawl_report(records, prepared.job_id, request.label, rejected_count)
                return JobResult(
                    job_id=prepared.job_id,
                    label=request.label,
                    records=tuple(records),
                    rejected_count=rejected_count,
                    job_dir=prepared.job_dir,
                )

            self._cleanup_staging_assets(
                prepared.template_dir,
                rows,
                self._config.template.workbook_name,
            )

            tracker.publish(records, stage="正在写入固定模板")
            self._log(callbacks, "INFO", f"正在将 {len(rows)} 条记录写入模板副本。")
            write_result = await asyncio.to_thread(
                self._excel_writer.write,
                prepared.template_dir,
                rows,
            )
            if cancellation.is_set():
                self._template_manager.assert_source_unchanged(prepared)
                self._write_crawl_report(records, prepared.job_id, request.label, rejected_count)
                return self._cancelled_result(request, prepared, records, rejected_count)

            await asyncio.to_thread(
                self._asset_validator,
                prepared.template_dir,
                set(write_result.inspection.referenced_assets),
                self._config.template.workbook_name,
            )
            self._template_manager.assert_source_unchanged(prepared)
            tracker.publish(records, stage="正在生成 template.zip")
            archive_path = await asyncio.to_thread(
                self._packager,
                prepared.template_dir,
                prepared.archive_path,
                self._config.template.archive_root_name,
            )
            self._template_manager.assert_source_unchanged(prepared)
            for record in records:
                if record.status == RecordStatus.READY_FOR_EXPORT:
                    record.status = RecordStatus.EXPORTED
                    _call(callbacks.record_updated, record)
            tracker.publish(records, stage="已完成")
            self._log(callbacks, "SUCCESS", f"任务完成：{archive_path}")
            self._write_crawl_report(records, prepared.job_id, request.label, rejected_count)
            return JobResult(
                job_id=prepared.job_id,
                label=request.label,
                records=tuple(records),
                rejected_count=rejected_count,
                job_dir=prepared.job_dir,
                archive_path=archive_path,
            )
        except asyncio.CancelledError:
            cancellation.set()
            if prepared is not None:
                prepared.archive_path.unlink(missing_ok=True)
            raise
        except TaskRunnerError:
            if prepared is not None:
                prepared.archive_path.unlink(missing_ok=True)
            raise
        except Exception as error:
            if prepared is not None:
                prepared.archive_path.unlink(missing_ok=True)
                try:
                    self._template_manager.assert_source_unchanged(prepared)
                except Exception as integrity_error:
                    raise TaskRunnerError(f"源模板完整性检查失败：{integrity_error}") from error
            raise TaskRunnerError(_friendly_error(error)) from error

    async def _resolve_tasks(self, request: JobRequest) -> tuple[tuple[UrlTask, ...], int]:
        if request.tasks:
            return request.tasks, 0
        assert request.input_path is not None
        read_result = await asyncio.to_thread(
            read_url_input,
            request.input_path,
            request.sheet_name,
        )
        return read_result.tasks, read_result.duplicate_or_invalid_count

    def _build_rows(
        self,
        records: list[RecordResult],
        callbacks: RunnerCallbacks,
    ) -> list[TemplateRow]:
        rows: list[TemplateRow] = []
        used_rows: dict[str, int] = defaultdict(int)
        for record in records:
            if record.status != RecordStatus.ASSETS_READY:
                continue
            record.status = RecordStatus.READY_FOR_EXPORT
            try:
                row = self._row_mapper.map(record)
                layout = get_sheet_layout(row.sheet_name)
                if used_rows[row.sheet_name] >= layout.max_rows:
                    record.status = RecordStatus.NEEDS_REVIEW
                    record.errors.append(
                        TaskError(
                            "export_validation",
                            "TEMPLATE_CAPACITY_EXCEEDED",
                            f"{row.sheet_name}最多可写 {layout.max_rows} 条，当前记录未写入。",
                            retryable=False,
                        )
                    )
                else:
                    rows.append(row)
                    used_rows[row.sheet_name] += 1
            except TemplateRowMappingError as error:
                record.status = RecordStatus.NEEDS_REVIEW
                record.errors.append(
                    TaskError("export_validation", "ROW_MAPPING_FAILED", str(error))
                )
            _call(callbacks.record_updated, record)
        return rows

    @staticmethod
    def _cleanup_staging_assets(
        template_dir: Path,
        rows: list[TemplateRow],
        workbook_name: str,
    ) -> None:
        """Remove staging files not referenced by any export row (failed-record leftovers)."""
        expected: set[str] = set()
        for row in rows:
            for name in row.all_asset_names():
                expected.add(name)
        for path in list(template_dir.rglob("*")):
            if path.is_dir():
                continue
            if path.name == workbook_name and path.parent == template_dir:
                continue
            if path.parent != template_dir:
                continue
            if path.name not in expected:
                path.unlink()

    @staticmethod
    def _cancelled_result(
        request: JobRequest,
        prepared: PreparedTemplate,
        records: Sequence[RecordResult],
        rejected_count: int,
    ) -> JobResult:
        prepared.archive_path.unlink(missing_ok=True)
        return JobResult(
            job_id=prepared.job_id,
            label=request.label,
            records=tuple(records),
            rejected_count=rejected_count,
            job_dir=prepared.job_dir,
            cancelled=True,
        )

    @staticmethod
    def _write_crawl_report(
        records: list[RecordResult],
        job_id: str,
        label: str,
        rejected_count: int,
    ) -> None:
        """⚠️ 临时功能：将本次运行结果写入爬取报告。项目完成后需删除此方法。"""
        try:
            append_run_report(records, job_id, label, rejected_count)
        except Exception as exc:
            # 报告写入失败不影响主流程
            pass

    @staticmethod
    def _log(
        callbacks: RunnerCallbacks,
        level: str,
        message: str,
        evidence_id: int | None = None,
    ) -> None:
        _call(
            callbacks.log,
            LogEvent(datetime.now(DEFAULT_TIMEZONE), level, message, evidence_id),
        )


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
        TaskRunner._log(
            self._callbacks,
            level,
            f"#{event.evidence_id:03d} {event.message}",
            event.evidence_id,
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


def _event_stage_text(event: TaskEvent) -> str:
    return {
        "start": "正在抓取网页",
        "retry": "正在重试",
        "finish": "已完成一条记录",
    }.get(event.stage, event.message)


def _call(callback: Callable[[Any], None] | None, value: Any) -> None:
    if callback is None:
        return
    try:
        callback(value)
    except Exception:
        pass


def _new_job_id() -> str:
    timestamp = datetime.now(DEFAULT_TIMEZONE).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _copy_retained_records(
    records: tuple[RecordResult, ...],
    template_dir: Path,
) -> list[RecordResult]:
    copied: list[RecordResult] = []
    for source_record in records:
        if source_record.status != RecordStatus.EXPORTED:
            continue
        record = deepcopy(source_record)
        record.assets.page_screenshot = _copy_asset(
            source_record.assets.page_screenshot,
            template_dir,
        )
        record.assets.author_screenshot = _copy_asset(
            source_record.assets.author_screenshot,
            template_dir,
        )
        record.assets.downloaded_images = []
        record.status = RecordStatus.ASSETS_READY
        copied.append(record)
    return copied


def _copy_asset(source: Path | None, template_dir: Path) -> Path | None:
    if source is None:
        return None
    source = Path(source)
    if not source.is_file():
        raise TaskRunnerError(f"重试所需的历史附件不存在：{source.name}")
    destination = template_dir / require_safe_file_name(source.name)
    if destination.exists():
        raise TaskRunnerError(f"重试附件文件名冲突：{destination.name}")
    shutil.copy2(source, destination)
    return destination


def _friendly_error(error: Exception) -> str:
    if isinstance(error, ExcelAutomationUnavailable):
        return "无法调用 Microsoft Excel。请确认已安装桌面版 Excel 和 pywin32，然后重试。"
    if isinstance(error, TemplateIntegrityError):
        return f"模板副本校验失败：{error}"
    if isinstance(error, BrowserUnavailableError):
        return "无法启动 Chromium。请先运行 python -m playwright install chromium。"
    if isinstance(error, InputReadError):
        return f"无法读取 URL 文件：{error}"
    if isinstance(error, FileNotFoundError):
        return f"缺少任务所需文件：{error}"
    return str(error) or type(error).__name__
