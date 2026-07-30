"""Coordinate input, crawling, fixed-template export, cancellation and progress."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.config.settings import AppConfig, TaskConfig
from src.crawler.engine import CrawlEngine
from src.services.headed_retry import retry_failed_records_headed
from src.crawler.platform_router import PlatformRouter
from src.domain.models import (
    RecordResult,
    RecordStatus,
    TaskEvent,
    TemplateRow,
    UrlTask,
)
from src.export.excel_writer import ExcelAutomationUnavailable, ExcelTemplateWriter, TemplateIntegrityError
from src.export.ooxml_writer import OoxmlTemplateWriter
from src.export.package_validator import validate_template_assets
from src.export.packager import create_template_archive
from src.export.row_mapper import TemplateRowMapper
from src.export.staging_assets import cleanup_staging_assets
from src.export.template_manager import PreparedTemplate, TemplateManager
from src.input.reader import InputReadError, read_url_input
from src.screenshot.author_evidence import AuthorEvidenceDecision
from src.services.export_flow import (
    audit_and_archive_author_evidence,
    build_export_rows,
)
from src.services.models import (
    JobRequest,
    JobResult,
    JobSummary,
    LogEvent,
    ProgressSnapshot,
    RunnerCallbacks,
)
from src.services.checkpoint_store import CheckpointStore
from src.services.override_flow import OverrideFlowError, apply_job_overrides
from src.services.progress_tracker import _call, _ProgressTracker
from src.services.retained_records import prepare_retained_records
from src.utils.time_utils import DEFAULT_TIMEZONE
from src.screenshot.browser import BrowserUnavailableError
from src.tools.quality_report import QualityArtifacts, write_quality_artifacts

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
        excel_writer: ExcelTemplateWriter | OoxmlTemplateWriter | None = None,
        platform_router: PlatformRouter | None = None,
        asset_validator: Callable[..., Any] = validate_template_assets,
        packager: Callable[..., Path] = create_template_archive,
    ) -> None:
        self._config = config
        self._template_manager = template_manager or TemplateManager(config.template)
        self._engine_factory = engine_factory or CrawlEngine
        self._row_mapper = row_mapper or TemplateRowMapper(
            config.task.export_content_max_chars
        )
        self._excel_writer = excel_writer or OoxmlTemplateWriter(
            config.template.workbook_name
        )
        self._platform_router = platform_router or PlatformRouter()
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
        checkpoint: CheckpointStore | None = None
        try:
            tasks, rejected_count = await self._resolve_tasks(request)
            if not tasks:
                raise TaskRunnerError("输入文件中没有可处理的 HTTP(S) URL。")
            job_id = request.job_id or _new_job_id()
            _call(callbacks.started, JobSummary(job_id, request.label, len(tasks), rejected_count))
            self._log(callbacks, "INFO", f"已读取 {len(tasks)} 条有效 URL，开始准备任务目录。")
            prepared = await asyncio.to_thread(self._template_manager.prepare, job_id)
            checkpoint = CheckpointStore(
                prepared.job_dir / "job_checkpoint.json",
                job_id=job_id,
                tasks=tasks,
            )
            if cancellation.is_set():
                checkpoint.save()
                return self._cancelled_result(
                    request,
                    prepared,
                    (),
                    rejected_count,
                    checkpoint.path,
                )

            tracker = _ProgressTracker(tasks, callbacks)
            retained = await asyncio.to_thread(
                prepare_retained_records,
                request,
                tasks,
                prepared.template_dir,
            )
            for record in retained:
                _call(callbacks.record_updated, record)
            if retained:
                checkpoint.update_many(retained)
            tracker.publish(stage="正在启动浏览器")
            auth_store_dir = self._config.task.auth_store_dir
            if auth_store_dir is not None:
                self._log(
                    callbacks,
                    "INFO",
                    f"已启用逐平台加密登录态：{Path(auth_store_dir).expanduser().resolve()}",
                )
            login_state = self._config.task.storage_state_path
            if login_state is not None:
                login_state = Path(login_state).expanduser().resolve()
                if login_state.is_file():
                    self._log(
                        callbacks,
                        "INFO",
                        f"未建立单平台状态时将兼容读取旧版综合登录态：{login_state}",
                    )
                elif auth_store_dir is None and not self._config.task.headless:
                    self._log(
                        callbacks,
                        "INFO",
                        f"旧版模式将在任务结束时保存综合登录态：{login_state}",
                    )
            engine = self._engine_factory(self._config.task)

            def on_result(record: RecordResult) -> None:
                tracker.on_record(record)
                checkpoint.update(record)

            retained_ids = {
                record.task.evidence_id
                for record in retained
            }
            pending_tasks = [
                task
                for task in tasks
                if task.evidence_id not in retained_ids
            ]
            if retained and request.resume_checkpoint_path is not None:
                self._log(
                    callbacks,
                    "INFO",
                    f"已从 checkpoint 复用 {len(retained)} 条记录，"
                    f"仅抓取其余 {len(pending_tasks)} 条。",
                )
            records = (
                await engine.run(
                    pending_tasks,
                    prepared.template_dir,
                    on_event=tracker.on_task_event,
                    on_result=on_result,
                    cancel_event=cancellation,
                )
                if pending_tasks
                else []
            )
            records = [*retained, *records]
            records.sort(key=lambda item: item.task.evidence_id)
            if cancellation.is_set() or any(
                record.status == RecordStatus.CANCELLED for record in records
            ):
                self._template_manager.assert_source_unchanged(prepared)
                tracker.publish(records, stage="任务已取消")
                self._log(callbacks, "WARNING", "任务已取消，已完成结果仅供查看，不生成压缩包。")
                checkpoint.update_many(records)
                return self._cancelled_result(
                    request,
                    prepared,
                    records,
                    rejected_count,
                    checkpoint.path,
                )

            if self._config.task.enable_headed_fallback and self._config.task.headless:
                await retry_failed_records_headed(
                    records,
                    task_config=self._config.task,
                    engine_factory=self._engine_factory,
                    output_dir=prepared.template_dir,
                    on_event=tracker.on_task_event,
                    on_result=on_result,
                    cancel_event=cancellation,
                    log=lambda level, message: self._log(callbacks, level, message),
                )

            try:
                override_count, staged_count = apply_job_overrides(
                    resume_checkpoint_path=request.resume_checkpoint_path,
                    job_dir=prepared.job_dir,
                    template_dir=prepared.template_dir,
                    records=records,
                )
            except OverrideFlowError as error:
                raise TaskRunnerError(str(error)) from error
            if override_count:
                self._log(
                    callbacks,
                    "INFO",
                    f"已合并 {override_count} 条人工补录记录，暂存 {staged_count} 个人工附件。",
                )
            rows = self._build_rows(records, callbacks)
            checkpoint.update_many(records)
            if not rows:
                self._template_manager.assert_source_unchanged(prepared)
                tracker.publish(records, stage="没有可导出的记录")
                self._log(
                    callbacks,
                    "WARNING",
                    "没有记录能匹配固定模板平台，因此未生成空的 template.zip。",
                )
                return JobResult(
                    job_id=prepared.job_id,
                    label=request.label,
                    records=tuple(records),
                    rejected_count=rejected_count,
                    job_dir=prepared.job_dir,
                    checkpoint_path=checkpoint.path,
                )

            rows, author_decisions, author_audit_entries = (
                audit_and_archive_author_evidence(
                    prepared.template_dir,
                    rows,
                    prepared.job_dir,
                )
            )
            for entry in author_audit_entries:
                self._log(
                    callbacks,
                    "WARNING",
                    (
                        f"主页截图 {entry['file']} 未通过 ZIP 前审计"
                        f"（{entry['rejection_code']}），已从附件移除并转入待补录。"
                    ),
                    entry.get("evidence_id"),
                )
            cleanup_staging_assets(
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
                checkpoint.update_many(records)
                return self._cancelled_result(
                    request,
                    prepared,
                    records,
                    rejected_count,
                    checkpoint.path,
                )

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
            checkpoint.update_many(records)
            quality = await asyncio.to_thread(
                self._write_quality_artifacts,
                records,
                prepared.job_dir,
                prepared.job_id,
                request.label,
                rejected_count,
                author_decisions,
                author_audit_entries,
            )
            tracker.publish(records, stage="已完成")
            self._log(callbacks, "SUCCESS", f"任务完成：{archive_path}")
            self._log(
                callbacks,
                "INFO",
                f"质量报告：{quality.report_path}；待补录清单：{quality.manual_entry_path}",
            )
            return JobResult(
                job_id=prepared.job_id,
                label=request.label,
                records=tuple(records),
                rejected_count=rejected_count,
                job_dir=prepared.job_dir,
                archive_path=archive_path,
                quality_report_path=quality.report_path,
                quality_summary_path=quality.summary_path,
                manual_entry_path=quality.manual_entry_path,
                checkpoint_path=checkpoint.path,
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
        return build_export_rows(
            records,
            self._row_mapper,
            self._platform_router,
            callbacks.record_updated,
        )

    def _write_quality_artifacts(
        self,
        records: list[RecordResult],
        job_dir: Path,
        job_id: str,
        label: str,
        rejected_count: int,
        author_decisions: list[AuthorEvidenceDecision] | None = None,
        author_audit_entries: list[dict[str, Any]] | None = None,
    ) -> QualityArtifacts:
        return write_quality_artifacts(
            records,
            job_dir,
            job_id=job_id,
            label=label,
            rejected_count=rejected_count,
            router=self._platform_router,
            author_decisions=author_decisions,
            author_audit_entries=author_audit_entries,
        )

    @staticmethod
    def _cancelled_result(
        request: JobRequest,
        prepared: PreparedTemplate,
        records: Sequence[RecordResult],
        rejected_count: int,
        checkpoint_path: Path | None = None,
    ) -> JobResult:
        prepared.archive_path.unlink(missing_ok=True)
        return JobResult(
            job_id=prepared.job_id,
            label=request.label,
            records=tuple(records),
            rejected_count=rejected_count,
            job_dir=prepared.job_dir,
            cancelled=True,
            checkpoint_path=checkpoint_path,
        )

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


def _new_job_id() -> str:
    timestamp = datetime.now(DEFAULT_TIMEZONE).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid4().hex[:8]}"


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
