"""crash_log 崩溃钩子与 runner 最终 ZIP 复制测试。"""

from pathlib import Path
import time

from src.services.models import JobResult
from src.utils import crash_log
from src.webui.runner import (
    FINAL_ARCHIVE_NAME,
    _copy_final_archive,
    _discard_scratch_archive,
)
from src.webui.serialize import finished_payload


def test_crash_log_writes_uncaught_exception(tmp_path: Path) -> None:
    path = crash_log.install(tmp_path)
    try:
        try:
            raise RuntimeError("闪退现场")
        except RuntimeError as error:
            crash_log._excepthook(type(error), error, error.__traceback__)
        crash_log.loop_exception_handler(None, {"message": "loop 炸了"})
    finally:
        crash_log.uninstall()

    content = path.read_text(encoding="utf-8")
    assert "RuntimeError: 闪退现场" in content
    assert "asyncio：loop 炸了" in content


def test_crash_log_install_is_idempotent(tmp_path: Path) -> None:
    first = crash_log.install(tmp_path)
    try:
        assert crash_log.install(tmp_path) == first
    finally:
        crash_log.uninstall()


def test_copy_final_archive_places_final_zip_next_to_init(tmp_path: Path) -> None:
    source_dir = tmp_path / "job-new"
    source_dir.mkdir()
    archive = source_dir / "template.zip"
    archive.write_bytes(b"zip-bytes")
    original_dir = tmp_path / "job-original"
    original_dir.mkdir()
    result = JobResult(
        job_id="job-new",
        label="人工补录导出",
        records=(),
        rejected_count=0,
        job_dir=source_dir,
        archive_path=archive,
    )

    copied = _copy_final_archive(result, original_dir)

    assert copied == original_dir / FINAL_ARCHIVE_NAME
    assert copied.read_bytes() == b"zip-bytes"


def test_copy_final_archive_skips_when_unset(tmp_path: Path) -> None:
    result = JobResult(
        job_id="j", label="l", records=(), rejected_count=0, job_dir=tmp_path
    )

    assert _copy_final_archive(result, tmp_path) is None
    assert _copy_final_archive(result, None) is None


def test_finished_payload_carries_final_copy_path(tmp_path: Path) -> None:
    result = JobResult(
        job_id="j", label="l", records=(), rejected_count=0, job_dir=tmp_path
    )
    payload = finished_payload(result, tmp_path / "template_final.zip")

    assert payload["final_copy_path"].endswith("template_final.zip")
    # 补录导出：archive_path 直接指向锚定目录的最终包（scratch 副本已清理）
    assert payload["archive_path"].endswith("template_final.zip")
    assert finished_payload(result)["final_copy_path"] is None


def test_discard_scratch_archive_removes_duplicate_zip(tmp_path: Path) -> None:
    scratch = tmp_path / "job-new"
    scratch.mkdir()
    archive = scratch / "template.zip"
    archive.write_bytes(b"zip")
    anchor = tmp_path / "job-anchor"
    anchor.mkdir()
    final = anchor / "template_final.zip"
    final.write_bytes(b"zip")
    result = JobResult(
        job_id="job-new",
        label="人工补录导出",
        records=(),
        rejected_count=0,
        job_dir=scratch,
        archive_path=archive,
    )

    _discard_scratch_archive(result, final)
    assert not archive.exists()
    assert final.is_file()

    archive.write_bytes(b"zip")
    _discard_scratch_archive(result, None)  # 无最终包 → 不动
    assert archive.is_file()


def test_reexport_anchors_session_and_deliver_dir(
    monkeypatch, tmp_path: Path
) -> None:
    """补录导出后：会话锚定原任务目录、两个 ZIP 同目录、scratch zip 清理。"""

    from src.config.settings import AppConfig, TaskConfig, TemplateConfig
    from src.domain.models import RecordResult, RecordStatus, UrlTask
    from src.services.checkpoint_store import CheckpointStore
    from src.services.models import JobRequest
    from src.webui import runner as runner_module
    from src.webui.runner import EventSink, JobRunner

    anchor = tmp_path / "output" / "job-anchor"
    anchor.mkdir(parents=True)
    (anchor / "template.zip").write_bytes(b"init")
    task = UrlTask(1, "https://example.test/p/1", "https://example.test/p/1")
    record = RecordResult(task=task, status=RecordStatus.EXPORTED)
    store = CheckpointStore(
        anchor / "job_checkpoint.json", job_id="job-anchor", tasks=(task,)
    )
    store.update(record)
    store.save()
    scratch = tmp_path / "output" / "job-new"

    class _FakeTaskRunner:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, request, callbacks=None, cancel_event=None):
            scratch.mkdir(parents=True)
            archive = scratch / "template.zip"
            archive.write_bytes(b"final")
            CheckpointStore(
                scratch / "job_checkpoint.json", job_id="job-new", tasks=(task,)
            ).update(record)
            return JobResult(
                job_id="job-new",
                label="人工补录导出",
                records=(record,),
                rejected_count=0,
                job_dir=scratch,
                archive_path=archive,
                checkpoint_path=scratch / "job_checkpoint.json",
            )

    monkeypatch.setattr(runner_module, "TaskRunner", _FakeTaskRunner)
    config = AppConfig(
        template=TemplateConfig(output_dir=tmp_path / "output"),
        task=TaskConfig(auth_store_dir=tmp_path / "auth"),
    )
    jobs = JobRunner(lambda: config, EventSink())
    jobs.final_copy_dir = anchor
    ok, message = jobs.start(
        JobRequest(
            tasks=(task,),
            resume_checkpoint_path=anchor / "job_checkpoint.json",
            reexport_only=True,
            label="人工补录导出",
        )
    )
    assert ok, message
    deadline = time.time() + 10
    while time.time() < deadline:
        if jobs.result is not None and not jobs.is_running():
            break
        time.sleep(0.05)

    assert jobs.result is not None
    assert jobs.session is not None
    assert Path(jobs.session.job_dir) == anchor  # 会话不漂移到 scratch 目录
    assert (anchor / "template_final.zip").read_bytes() == b"final"
    assert not (scratch / "template.zip").exists()
    assert jobs.last_deliver_dir == anchor
    assert jobs.last_checkpoint == str(anchor / "job_checkpoint.json")
