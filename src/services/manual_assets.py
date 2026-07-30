"""Stage operator-captured manual assets into the export staging directory.

The review workspace saves fullscreen captures into ``<job_dir>/manual_assets``
and records only their safe file names in overrides.  At export time the
referenced files are copied into the staging ``template/`` directory so the
packager and validator see them exactly like crawler-produced screenshots.
Missing files never fail the export: the reference is dropped (the template
contract allows blank cells) and an auditable error is attached instead.
"""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Iterable

from src.domain.models import RecordResult, TaskError
from src.utils.file_utils import require_safe_file_name

MANUAL_ASSETS_DIR_NAME = "manual_assets"


def stage_manual_assets(
    job_dir: Path,
    template_dir: Path,
    records: Iterable[RecordResult],
) -> int:
    """Copy referenced manual assets into *template_dir*; return staged count."""

    source_dir = Path(job_dir) / MANUAL_ASSETS_DIR_NAME
    template_dir = Path(template_dir)
    staged = 0
    for record in records:
        primary = record.assets.page_screenshot
        manual_primary = primary is not None and not Path(primary).is_absolute()
        staged_path = _stage_path(
            record,
            primary,
            source_dir,
            template_dir,
            "主截图",
        )
        record.assets.page_screenshot = staged_path
        if staged_path is not None and manual_primary:
            staged += 1
        author = record.assets.author_screenshot
        manual_author = author is not None and not Path(author).is_absolute()
        staged_author = _stage_path(
            record,
            author,
            source_dir,
            template_dir,
            "个人页截图",
        )
        record.assets.author_screenshot = staged_author
        if staged_author is not None and manual_author:
            staged += 1
        extras: list[Path] = []
        for path in record.assets.extra_attachments:
            staged_extra = _stage_path(
                record, path, source_dir, template_dir, "附件"
            )
            if staged_extra is not None:
                extras.append(staged_extra)
        record.assets.extra_attachments = extras
        staged += len(extras)
    return staged


def _stage_path(
    record: RecordResult,
    path: Path | None,
    source_dir: Path,
    template_dir: Path,
    kind: str,
) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    if candidate.is_absolute() and candidate.exists():
        if candidate.parent == template_dir:
            return candidate
        target = template_dir / require_safe_file_name(candidate.name)
        if candidate.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
        return target
    name = candidate.name
    try:
        safe_name = require_safe_file_name(name)
    except Exception:
        record.errors.append(
            TaskError(
                "manual_override",
                "MANUAL_ASSET_NAME_INVALID",
                f"人工{kind}文件名不安全：{name!r}，已从导出中移除。",
                retryable=False,
            )
        )
        return None
    source = source_dir / safe_name
    if source.exists():
        target = template_dir / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target
    record.errors.append(
        TaskError(
            "manual_override",
            "MANUAL_ASSET_MISSING",
            f"人工{kind}文件不存在：{safe_name}，已从导出中移除。",
            retryable=False,
        )
    )
    return None
