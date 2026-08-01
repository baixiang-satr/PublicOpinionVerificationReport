"""Copy successfully exported records into a retry job's staging directory."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from src.domain.models import RecordResult, RecordStatus
from src.domain.models import UrlTask
from src.services.checkpoint_store import CheckpointStore
from src.services.models import JobRequest
from src.screenshot.author_evidence import decision_sidecar_name
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
        reusable = _is_reusable(source_record)
        if not reusable and not _is_completed(source_record, include_all_completed):
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
        _copy_author_decision(source_record, template_dir)
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
    # Merge source records before copying any attachment.  A freshly retried
    # record intentionally has the same evidence filename (for example
    # ``003.jpg``) as the checkpoint record it replaces.  Copying both first
    # incorrectly treated that normal override as a filename collision and
    # could also leave a stale homepage-decision sidecar behind.
    retained_by_id: dict[int, RecordResult] = {}
    if request.resume_checkpoint_path is not None:
        snapshot = CheckpointStore.load(
            request.resume_checkpoint_path,
            expected_tasks=tasks,
        )
        retained_by_id.update(
            {
                record.task.evidence_id: record
                for record in snapshot.records
                if _is_reusable(record)
                or _is_completed(record, request.reexport_only)
            }
        )
    retained_by_id.update(
        {
            record.task.evidence_id: record
            for record in request.retained_records
            if _is_reusable(record)
        }
    )
    selected = tuple(
        record
        for evidence_id, record in retained_by_id.items()
        if evidence_id not in request.retry_evidence_ids
    )
    return copy_retained_records(
        selected,
        template_dir,
        include_all_completed=request.reexport_only,
    )


def _is_reusable(record: RecordResult) -> bool:
    return record.status in {
        RecordStatus.ASSETS_READY,
        RecordStatus.READY_FOR_EXPORT,
        RecordStatus.EXPORTED,
    }


def _is_completed(record: RecordResult, include_all_completed: bool) -> bool:
    return include_all_completed and record.status not in {
        RecordStatus.PENDING,
        RecordStatus.RUNNING,
        RecordStatus.CANCELLED,
    }


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


def _copy_author_decision(source_record: RecordResult, template_dir: Path) -> None:
    """Carry the accepted/rejected homepage audit fact into a resumed job."""

    author = source_record.assets.author_screenshot
    if author is None:
        return
    author = Path(author)
    sidecar_name = decision_sidecar_name(source_record.task.evidence_id)
    candidates = [author.with_suffix(".decision.json")]
    # Normal completed jobs archive sidecars outside staging so cleanup keeps
    # them out of template.zip.  A checkpoint record still points to the
    # staging image, from which its job root is deterministic.
    if len(author.parents) >= 3:
        candidates.append(author.parents[2] / "author_decisions" / sidecar_name)
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        return
    destination = Path(template_dir) / sidecar_name
    if destination.exists():
        return
    shutil.copy2(source, destination)
