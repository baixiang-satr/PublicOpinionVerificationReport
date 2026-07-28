from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.domain.models import (
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    TaskError,
    TaskEvent,
    UrlTask,
)
from src.services.models import JobRequest, RunnerCallbacks
from src.services.task_runner import TaskRunner


class FakeEngine:
    def __init__(self, status: RecordStatus = RecordStatus.ASSETS_READY) -> None:
        self.status = status
        self.called = False

    async def run(
        self,
        tasks: list[UrlTask],
        output_dir: Path,
        on_event=None,
        on_result=None,
        cancel_event=None,
    ) -> list[RecordResult]:
        self.called = True
        results: list[RecordResult] = []
        for task in tasks:
            if on_event:
                on_event(TaskEvent(task.evidence_id, RecordStatus.RUNNING, "start", "开始"))
            screenshot = None
            if self.status == RecordStatus.ASSETS_READY:
                screenshot = output_dir / f"{task.evidence_id:03d}.jpg"
                screenshot.write_bytes(b"jpeg")
            result = RecordResult(
                task=task,
                status=self.status,
                page=PageData(
                    final_url=task.normalized_url,
                    title="测试标题",
                    content_summary="测试正文",
                    author_name="测试作者",
                    status_code=200,
                ),
                route=RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
            )
            result.assets.page_screenshot = screenshot
            if self.status == RecordStatus.FAILED:
                result.errors.append(TaskError("navigation", "TEST_FAILURE", "测试失败"))
            results.append(result)
            if on_result:
                on_result(result)
            if on_event:
                on_event(TaskEvent(task.evidence_id, self.status, "finish", self.status.value))
        return results


class FakeWriter:
    def __init__(self) -> None:
        self.calls = 0
        self.rows: list[object] = []

    def write(self, _template_dir: Path, rows: list[object]):
        self.calls += 1
        self.rows = list(rows)
        assets = tuple(
            sorted(
                name
                for row in rows
                for name in row.all_asset_names()
            )
        )
        return SimpleNamespace(inspection=SimpleNamespace(referenced_assets=assets))


def _config(tmp_path: Path) -> AppConfig:
    source = tmp_path / "source-template"
    source.mkdir()
    (source / "template.xlsx").write_bytes(b"fixed-template")
    template = TemplateConfig(source_dir=source, output_dir=tmp_path / "output")
    return AppConfig(template=template, task=TaskConfig())


@pytest.mark.asyncio
async def test_runner_exports_valid_records_and_reports_progress(tmp_path: Path) -> None:
    config = _config(tmp_path)
    engine = FakeEngine()
    writer = FakeWriter()
    snapshots = []
    records = []
    request = JobRequest(
        tasks=(
            UrlTask(1, "https://example.test/1", "https://example.test/1"),
            UrlTask(2, "https://example.test/2", "https://example.test/2"),
        ),
        job_id="success-job",
    )
    runner = TaskRunner(
        config,
        engine_factory=lambda _task_config: engine,
        excel_writer=writer,
    )

    result = await runner.run(
        request,
        callbacks=RunnerCallbacks(
            progress=snapshots.append,
            record_updated=records.append,
        ),
    )

    assert writer.calls == 1
    assert result.archive_path is not None and result.archive_path.is_file()
    assert result.quality_report_path is not None
    assert result.quality_report_path.is_file()
    assert result.quality_summary_path is not None
    assert result.quality_summary_path.is_file()
    assert result.manual_entry_path is not None
    assert result.manual_entry_path.is_file()
    assert [record.status for record in result.records] == [
        RecordStatus.EXPORTED,
        RecordStatus.EXPORTED,
    ]
    assert snapshots[-1].stage == "已完成"
    assert snapshots[-1].ready == 2
    assert any(record.status == RecordStatus.EXPORTED for record in records)
    with ZipFile(result.archive_path) as archive:
        assert archive.namelist() == [
            "template/001.jpg",
            "template/002.jpg",
            "template/template.xlsx",
        ]


@pytest.mark.asyncio
async def test_runner_exports_placeholder_rows_when_all_records_fail(tmp_path: Path) -> None:
    config = _config(tmp_path)
    writer = FakeWriter()
    runner = TaskRunner(
        config,
        engine_factory=lambda _task_config: FakeEngine(RecordStatus.FAILED),
        excel_writer=writer,
    )

    result = await runner.run(
        JobRequest(
            tasks=(UrlTask(1, "https://example.test/1", "https://example.test/1"),),
            job_id="failed-job",
        )
    )

    assert writer.calls == 1
    assert [row.evidence_id for row in writer.rows] == [1]
    assert result.archive_path is not None
    assert (result.job_dir / "template.zip").is_file()
    assert result.manual_entry_path is not None
    assert "001" in result.manual_entry_path.read_text(encoding="utf-8-sig")
    assert result.retryable_tasks == (result.records[0].task,)
    with ZipFile(result.archive_path) as archive:
        assert archive.namelist() == ["template/template.xlsx"]


@pytest.mark.asyncio
async def test_runner_honors_cancellation_before_browser_start(tmp_path: Path) -> None:
    import asyncio

    config = _config(tmp_path)
    engine = FakeEngine()
    cancel_event = asyncio.Event()
    cancel_event.set()
    runner = TaskRunner(
        config,
        engine_factory=lambda _task_config: engine,
        excel_writer=FakeWriter(),
    )

    result = await runner.run(
        JobRequest(
            tasks=(UrlTask(1, "https://example.test/1", "https://example.test/1"),),
            job_id="cancelled-job",
        ),
        cancel_event=cancel_event,
    )

    assert result.cancelled
    assert result.archive_path is None
    assert not engine.called


@pytest.mark.asyncio
async def test_runner_reads_input_and_reports_rejected_values(tmp_path: Path) -> None:
    config = _config(tmp_path)
    input_path = tmp_path / "urls.txt"
    input_path.write_text(
        "https://example.test/1\nnot-a-url\nhttps://example.test/1",
        encoding="utf-8",
    )
    summaries = []
    runner = TaskRunner(
        config,
        engine_factory=lambda _task_config: FakeEngine(RecordStatus.FAILED),
        excel_writer=FakeWriter(),
    )

    result = await runner.run(
        JobRequest(input_path=input_path, job_id="input-job"),
        callbacks=RunnerCallbacks(started=summaries.append),
    )

    assert summaries[0].total == 2
    assert summaries[0].rejected_count == 1
    assert result.rejected_count == 1


@pytest.mark.asyncio
async def test_retry_keeps_previous_exports_and_adds_recovered_record(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = TaskRunner(
        config,
        engine_factory=lambda _task_config: FakeEngine(),
        excel_writer=FakeWriter(),
    )
    first = await runner.run(
        JobRequest(
            tasks=(UrlTask(1, "https://example.test/1", "https://example.test/1"),),
            job_id="first-job",
        )
    )

    retried = await runner.run(
        JobRequest(
            tasks=(UrlTask(2, "https://example.test/2", "https://example.test/2"),),
            retained_records=first.records,
            job_id="retry-job",
            label="失败项重试",
        )
    )

    assert [record.task.evidence_id for record in retried.records] == [1, 2]
    assert all(record.status == RecordStatus.EXPORTED for record in retried.records)
    assert retried.archive_path is not None
    with ZipFile(retried.archive_path) as archive:
        assert archive.namelist() == [
            "template/001.jpg",
            "template/002.jpg",
            "template/template.xlsx",
        ]


@pytest.mark.asyncio
async def test_resume_checkpoint_reexports_without_recrawling(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    tasks = (
        UrlTask(1, "https://example.test/1", "https://example.test/1"),
        UrlTask(2, "https://example.test/2", "https://example.test/2"),
    )
    first = await TaskRunner(
        config,
        engine_factory=lambda _task_config: FakeEngine(),
        excel_writer=FakeWriter(),
    ).run(JobRequest(tasks=tasks, job_id="checkpoint-source"))
    assert first.checkpoint_path is not None

    engine = FakeEngine()
    resumed = await TaskRunner(
        config,
        engine_factory=lambda _task_config: engine,
        excel_writer=FakeWriter(),
    ).run(
        JobRequest(
            tasks=tasks,
            job_id="checkpoint-resumed",
            resume_checkpoint_path=first.checkpoint_path,
        )
    )

    assert not engine.called
    assert [record.task.evidence_id for record in resumed.records] == [1, 2]
    assert all(record.status == RecordStatus.EXPORTED for record in resumed.records)
    assert resumed.archive_path is not None


@pytest.mark.asyncio
async def test_reexport_only_keeps_failed_placeholder_without_recrawling(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    tasks = (UrlTask(1, "https://example.test/1", "https://example.test/1"),)
    first = await TaskRunner(
        config,
        engine_factory=lambda _task_config: FakeEngine(RecordStatus.FAILED),
        excel_writer=FakeWriter(),
    ).run(JobRequest(tasks=tasks, job_id="failed-checkpoint-source"))
    assert first.checkpoint_path is not None

    engine = FakeEngine()
    resumed = await TaskRunner(
        config,
        engine_factory=lambda _task_config: engine,
        excel_writer=FakeWriter(),
    ).run(
        JobRequest(
            tasks=tasks,
            job_id="failed-checkpoint-reexport",
            resume_checkpoint_path=first.checkpoint_path,
            reexport_only=True,
        )
    )

    assert not engine.called
    assert resumed.records[0].status == RecordStatus.FAILED
    assert resumed.archive_path is not None


@pytest.mark.asyncio
async def test_checkpoint_can_retry_only_selected_evidence_ids(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    tasks = (
        UrlTask(1, "https://example.test/1", "https://example.test/1"),
        UrlTask(2, "https://example.test/2", "https://example.test/2"),
    )
    first = await TaskRunner(
        config,
        engine_factory=lambda _task_config: FakeEngine(),
        excel_writer=FakeWriter(),
    ).run(JobRequest(tasks=tasks, job_id="selected-retry-source"))
    assert first.checkpoint_path is not None

    engine = FakeEngine()
    resumed = await TaskRunner(
        config,
        engine_factory=lambda _task_config: engine,
        excel_writer=FakeWriter(),
    ).run(
        JobRequest(
            tasks=tasks,
            job_id="selected-retry-result",
            resume_checkpoint_path=first.checkpoint_path,
            reexport_only=True,
            retry_evidence_ids=(2,),
        )
    )

    assert engine.called
    assert [record.task.evidence_id for record in resumed.records] == [1, 2]


def test_build_rows_preserves_all_records_beyond_seeded_sheet_rows(tmp_path: Path) -> None:
    runner = TaskRunner(
        _config(tmp_path),
        engine_factory=lambda _task_config: FakeEngine(),
        excel_writer=FakeWriter(),
    )
    records = []
    for evidence_id in range(1, 4):
        record = RecordResult(
            task=UrlTask(
                evidence_id,
                f"https://item.jd.com/{evidence_id}.html",
                f"https://item.jd.com/{evidence_id}.html",
            ),
            status=RecordStatus.ASSETS_READY,
            page=PageData(title=f"商品 {evidence_id}"),
            route=RouteDecision("电商平台", "京东_京东商城_电商平台", "商家"),
        )
        record.assets.page_screenshot = Path(f"{evidence_id:03d}.jpg")
        records.append(record)

    rows = runner._build_rows(records, RunnerCallbacks())

    assert [row.evidence_id for row in rows] == [1, 2, 3]
    assert all(record.status == RecordStatus.READY_FOR_EXPORT for record in records)
    assert all(
        error.code != "TEMPLATE_CAPACITY_EXCEEDED"
        for record in records
        for error in record.errors
    )


def test_build_rows_routes_failed_record_from_submitted_url(tmp_path: Path) -> None:
    runner = TaskRunner(
        _config(tmp_path),
        engine_factory=lambda _task_config: FakeEngine(),
        excel_writer=FakeWriter(),
    )
    record = RecordResult(
        task=UrlTask(
            1,
            "https://item.jd.com/100033296948.html",
            "https://item.jd.com/100033296948.html",
        ),
        status=RecordStatus.NEEDS_REVIEW,
        page=PageData(final_url="https://www.jd.com/"),
    )

    rows = runner._build_rows([record], RunnerCallbacks())

    assert len(rows) == 1
    assert rows[0].sheet_name == "电商平台"
    assert rows[0].values_by_column["A"] == record.task.original_url
    assert rows[0].values_by_column["B"] == "京东_京东商城_电商平台"
    assert rows[0].primary_screenshot_name is None
    assert record.status == RecordStatus.NEEDS_REVIEW
