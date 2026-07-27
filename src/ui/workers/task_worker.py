"""Run the asyncio task pipeline inside an owned QThread."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import threading
from collections.abc import Callable

from PyQt5.QtCore import QThread, pyqtSignal

from src.config.settings import AppConfig
from src.services.models import JobRequest, RunnerCallbacks
from src.services.task_runner import TaskRunner


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

    def cancel(self) -> None:
        self._cancel_requested.set()
        self.requestInterruption()

    def run(self) -> None:
        try:
            asyncio.run(self._execute())
        except Exception as error:
            self.job_failed.emit(str(error))

    async def _execute(self) -> None:
        cancel_event = asyncio.Event()
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
            self.job_finished.emit(result)
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor

    async def _monitor_cancellation(self, cancel_event: asyncio.Event) -> None:
        while not self._cancel_requested.is_set():
            await asyncio.sleep(0.05)
        cancel_event.set()
