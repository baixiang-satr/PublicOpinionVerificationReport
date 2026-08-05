"""crash_log 崩溃钩子与 runner 最终 ZIP 复制测试。"""

from pathlib import Path

from src.services.models import JobResult
from src.utils import crash_log
from src.webui.runner import FINAL_ARCHIVE_NAME, _copy_final_archive
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
    assert finished_payload(result)["final_copy_path"] is None
