from __future__ import annotations

import asyncio
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QEventLoop, QTimer

from src.config.settings import AppConfig, TemplateConfig
from src.domain.models import UrlTask
from src.services.models import JobRequest, JobResult, JobSummary
from src.ui.app import create_application
from src.ui.workers.task_worker import TaskWorker


pytestmark = pytest.mark.ui


class FakeRunner:
    def __init__(self, result: JobResult) -> None:
        self.result = result

    async def run(self, request, callbacks, cancel_event):
        callbacks.started(JobSummary(self.result.job_id, request.label, 1, 0))
        await asyncio.sleep(0.01)
        return self.result


def test_task_worker_returns_result_without_blocking_gui_thread(tmp_path: Path) -> None:
    app = create_application([])
    source = tmp_path / "template"
    source.mkdir()
    (source / "template.xlsx").write_bytes(b"template")
    config = AppConfig(
        template=TemplateConfig(source_dir=source, output_dir=tmp_path / "output")
    )
    request = JobRequest(
        tasks=(UrlTask(1, "https://example.test/1", "https://example.test/1"),)
    )
    expected = JobResult("worker-job", "批量抓取", (), 0, tmp_path)
    worker = TaskWorker(
        config,
        request,
        runner_factory=lambda _config: FakeRunner(expected),
    )
    received = []
    loop = QEventLoop()
    worker.job_finished.connect(received.append)
    worker.finished.connect(loop.quit)
    worker.start()
    QTimer.singleShot(3_000, loop.quit)
    loop.exec_()
    app.processEvents()

    assert received == [expected]
    assert not worker.isRunning()
