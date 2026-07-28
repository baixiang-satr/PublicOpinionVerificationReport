"""Run the asyncio task pipeline inside an owned QThread."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import threading
from collections.abc import Callable

from PyQt5.QtCore import QThread, pyqtSignal

from src.config.settings import AppConfig
from src.services.models import JobRequest, JobResult, RunnerCallbacks
from src.services.task_runner import TaskRunner
from src.utils.time_utils import DEFAULT_TIMEZONE

# ⚠️ 临时功能：爬取运行报告，项目完成后需删除
from src.tools.crawl_tracker import append_run_report  # noqa: F811


class TaskWorker(QThread):
    job_started = pyqtSignal(object)
    task_event = pyqtSignal(object)
    record_updated = pyqtSignal(object)
    progress_changed = pyqtSignal(object)
    log_message = pyqtSignal(object)
    job_finished = pyqtSignal(object)
    job_failed = pyqtSignal(str)

    def __init__(
        self,
        config: AppConfig,
        request: JobRequest,
        parent=None,
        runner_factory: Callable[[AppConfig], TaskRunner] | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._request = request
        self._runner_factory = runner_factory or TaskRunner
        self._cancel_requested = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_cancel_event: asyncio.Event | None = None

    def cancel(self) -> None:
        self._cancel_requested.set()
        self.requestInterruption()
        loop = self._loop
        cancel_event = self._async_cancel_event
        if loop is not None and cancel_event is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(cancel_event.set)
            except RuntimeError:
                # The worker loop may close between is_running() and this call.
                pass

    def run(self) -> None:
        try:
            asyncio.run(self._execute())
        except Exception as error:
            self._write_error_report(str(error))
            self.job_failed.emit(str(error))

    async def _execute(self) -> None:
        cancel_event = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._async_cancel_event = cancel_event
        if self._cancel_requested.is_set():
            cancel_event.set()
        monitor = asyncio.create_task(self._monitor_cancellation(cancel_event))
        callbacks = RunnerCallbacks(
            started=self.job_started.emit,
            task_event=self.task_event.emit,
            record_updated=self.record_updated.emit,
            progress=self.progress_changed.emit,
            log=self.log_message.emit,
        )
        try:
            runner = self._runner_factory(self._config)
            result = await runner.run(
                self._request,
                callbacks=callbacks,
                cancel_event=cancel_event,
            )
            self._write_success_report(result)
            self.job_finished.emit(result)
        except asyncio.CancelledError:
            self._write_error_report("任务被取消")
            raise
        except Exception as error:
            self._write_error_report(str(error))
            raise
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
            self._async_cancel_event = None
            self._loop = None

    def _write_success_report(self, result: JobResult) -> None:
        """⚠️ 临时功能：任务成功后写入爬取报告。"""
        try:
            append_run_report(
                list(result.records),
                result.job_id,
                result.label,
                result.rejected_count,
            )
        except Exception:
            pass

    def _write_error_report(self, error_message: str) -> None:
        """⚠️ 临时功能：任务失败时写入错误报告。"""
        try:
            now = datetime.now(DEFAULT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            label = getattr(self._request, "label", "批量抓取")
            report_lines = [
                "\n\n---\n",
                f"## 运行报告：{now}\n",
                "| 项目 | 数值 |",
                "|------|------|",
                f"| **任务名称** | {label} |",
                f"| **处理时间** | {now} |",
                "| **状态** | ❌ 任务失败 |",
                f"| **错误信息** | {error_message} |",
                "\n### ❌ 任务执行失败\n",
                f"任务未能完成执行，错误原因：{error_message}\n\n",
                "请检查输入文件和配置后重试。\n",
            ]
            from pathlib import Path
            report_file = Path(__file__).resolve().parents[3] / "docs" / "crawl_run_report.md"
            report_file.parent.mkdir(parents=True, exist_ok=True)
            if not report_file.exists():
                header = "# 爬取运行报告\n\n> ⚠️ **临时文件** — 项目完成后将删除此功能。\n\n每次运行的结果将追加到本文档末尾。\n"
                report_file.write_text(header, encoding="utf-8")
            with open(report_file, "a", encoding="utf-8") as f:
                f.write("\n".join(report_lines))
        except Exception:
            pass

    async def _monitor_cancellation(self, cancel_event: asyncio.Event) -> None:
        while not self._cancel_requested.is_set():
            await asyncio.sleep(0.05)
        cancel_event.set()
