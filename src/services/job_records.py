"""Checkpoint read/write helpers for hand-built record sets.

Normal jobs let :class:`~src.services.task_runner.TaskRunner` own the
checkpoint.  Two features need to write one directly instead:

- ``TemplateZipImporter`` reconstructs records from an uploaded template.zip.
- The review grid lets operators add/remove fully manual rows
  (群聊 / 朋友圈 have no URL column, so they are never crawled).

Both reuse :class:`CheckpointStore` so resume/export semantics stay identical
to crawled jobs.
"""
from __future__ import annotations

from pathlib import Path
import shutil

from src.domain.models import RecordResult
from src.services import recovery_mirror
from src.services.checkpoint_store import CHECKPOINT_SCHEMA_VERSION, CheckpointStore

CHECKPOINT_FILE_NAME = "job_checkpoint.json"


def ensure_checkpoint(job_dir: Path, records: list[RecordResult]) -> Path:
    """保证断点文件存在：优先从恢复镜像还原，否则用内存记录重建。

    output/ 任务目录可能被外部清理，闪退也会留下缺失断点的目录；
    导出前调用本函数兜底，不再向用户报「找不到任务断点文件」。
    """

    job_dir = Path(job_dir)
    path = job_dir / CHECKPOINT_FILE_NAME
    if path.is_file():
        return path
    mirrored = recovery_mirror.mirrored_json(job_dir.name, CHECKPOINT_FILE_NAME)
    if mirrored is not None:
        try:
            shutil.copy2(mirrored, path)
            return path
        except OSError:
            pass
    return write_checkpoint(job_dir, job_dir.name, list(records))


def write_checkpoint(
    job_dir: Path,
    job_id: str,
    records: list[RecordResult],
) -> Path:
    """Atomically (re)write ``job_checkpoint.json`` for ``records``."""

    job_dir = Path(job_dir)
    tasks = tuple(record.task for record in records)
    store = CheckpointStore(
        job_dir / CHECKPOINT_FILE_NAME,
        job_id=job_id,
        tasks=tasks,
    )
    store.update_many(list(records))
    return store.path


def append_record(job_dir: Path, record: RecordResult) -> Path:
    """Add ``record`` to an existing checkpoint, keeping its job id."""

    job_dir = Path(job_dir)
    snapshot = CheckpointStore.load(job_dir / CHECKPOINT_FILE_NAME)
    records = [r for r in snapshot.records if r.task.evidence_id != record.task.evidence_id]
    records.append(record)
    return write_checkpoint(job_dir, snapshot.job_id, records)


def remove_record(job_dir: Path, evidence_id: int) -> Path:
    """Drop one record from the checkpoint (manual-row deletion)."""

    job_dir = Path(job_dir)
    snapshot = CheckpointStore.load(job_dir / CHECKPOINT_FILE_NAME)
    records = [r for r in snapshot.records if r.task.evidence_id != evidence_id]
    return write_checkpoint(job_dir, snapshot.job_id, records)


def checkpoint_exists(job_dir: Path) -> bool:
    return (Path(job_dir) / CHECKPOINT_FILE_NAME).is_file()


__all__ = [
    "CHECKPOINT_FILE_NAME",
    "CHECKPOINT_SCHEMA_VERSION",
    "append_record",
    "checkpoint_exists",
    "remove_record",
    "write_checkpoint",
]
