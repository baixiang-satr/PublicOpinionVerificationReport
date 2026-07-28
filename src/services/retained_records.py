"""Copy successfully exported records into a retry job's staging directory."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from src.domain.models import RecordResult, RecordStatus
from src.domain.models import UrlTask
from src.services.checkpoint_store import CheckpointStore
from src.services.models import JobRequest
from src.utils.file_utils import require_safe_file_name


class RetainedRecordError(RuntimeError):
    """A retained attachment cannot be reused safely."""


def copy_retained_records(
    records: tuple[RecordResult, ...],
    template_dir: Path,
    *,
    include_all_completed: bool = False,
) -> list[RecordResult]:
    copied: list[RecordResult] = []
    for source_record in records:
        reusable = source_record.status in {
            RecordStatus.ASSETS_READY,
            RecordStatus.READY_FOR_EXPORT,
            RecordStatus.EXPORTED,
        }
        if not reusable and not (
            include_all_completed
            and source_record.status
            not in {
                RecordStatus.PENDING,
                RecordStatus.RUNNING,
                RecordStatus.CANCELLED,
            }
        ):
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
        if reusable:
            record.status = RecordStatus.ASSETS_READY
        copied.append(record)
    return copied


def load_resumable_records(
    checkpoint_path: Path,
    *,
    tasks: tuple[UrlTask, ...],
    template_dir: Path,
    include_all_completed: bool = False,
) -> list[RecordResult]:
    snapshot = CheckpointStore.load(
        checkpoint_path,
        expected_tasks=tasks,
    )
    return copy_retained_records(
        snapshot.records,
        template_dir,
        include_all_completed=include_all_completed,
    )


def prepare_retained_records(
    request: JobRequest,
    tasks: tuple[UrlTask, ...],
    template_dir: Path,
) -> list[RecordResult]:
    retained = copy_retained_records(
        request.retained_records,
        template_dir,
    )
    if request.resume_checkpoint_path is None:
        return retained
    resumed = load_resumable_records(
        request.resume_checkpoint_path,
        tasks=tasks,
        template_dir=template_dir,
        include_all_completed=request.reexport_only,
    )
    retained_by_id = {
        record.task.evidence_id: record
        for record in [*resumed, *retained]
        if record.task.evidence_id not in request.retry_evidence_ids
    }
    return list(retained_by_id.values())


def _copy_asset(source: Path | None, template_dir: Path) -> Path | None:
    if source is None:
        return None
    source = Path(source)
    if not source.is_file():
        raise RetainedRecordError(f"重试所需的历史附件不存在：{source.name}")
    destination = template_dir / require_safe_file_name(source.name)
    if destination.exists():
        raise RetainedRecordError(f"重试附件文件名冲突：{destination.name}")
    shutil.copy2(source, destination)
    return destination
