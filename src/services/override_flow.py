"""Job-level manual override application, split from TaskRunner."""
from __future__ import annotations

from pathlib import Path

from src.domain.models import RecordResult
from src.services.manual_assets import stage_manual_assets
from src.services.override_apply import apply_overrides
from src.services.override_store import ManualOverrideStore


class OverrideFlowError(RuntimeError):
    """Manual override data could not be read or applied."""


def apply_job_overrides(
    *,
    resume_checkpoint_path: Path | None,
    job_dir: Path,
    template_dir: Path,
    records: list[RecordResult],
) -> tuple[int, int]:
    """Merge persisted manual overrides into records before export.

    Overrides live next to the checkpoint they were captured with (for a
    resumed/re-exported job that is the *original* job directory), while the
    staging template belongs to the current job.  Returns the number of
    overrides applied and manual asset files staged.
    """

    override_dir = (
        Path(resume_checkpoint_path).parent
        if resume_checkpoint_path is not None
        else Path(job_dir)
    )
    try:
        overrides = ManualOverrideStore(override_dir).load().all()
    except Exception as error:
        raise OverrideFlowError(f"人工补录数据读取失败：{error}") from error
    if not overrides:
        return 0, 0
    apply_overrides(records, overrides)
    staged = stage_manual_assets(override_dir, Path(template_dir), records)
    return len(overrides), staged
