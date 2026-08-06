"""删除记录时联动清理磁盘证据文件（仅限任务目录内，绝不越界）。

内容页/个人主页截图、OCR 临时图片与作者主页判定 sidecar 按证据号落在任务
目录，人工补录资产按文件名落在 ``manual_assets/``。记录被删除时这些文件
必须一并清理，否则任务目录会残留无主截图；恢复镜像（若启用）中的副本
同步删除。全部操作只告警不抛异常——清理失败绝不能阻断删除本身。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.domain.models import RecordResult
from src.domain.overrides import ManualOverride
from src.screenshot.author_evidence import decision_sidecar_name
from src.services import recovery_mirror
from src.services.manual_assets import MANUAL_ASSETS_DIR_NAME

logger = logging.getLogger(__name__)


def delete_record_artifacts(
    job_dir: Path,
    record: RecordResult,
    override: ManualOverride | None,
) -> tuple[Path, ...]:
    """删除 *record* 引用的全部证据文件；返回实际删除的路径。"""

    job_dir = Path(job_dir).resolve()
    manual_dir = job_dir / MANUAL_ASSETS_DIR_NAME
    candidates: list[Path] = []
    for path in (
        record.assets.page_screenshot,
        record.assets.author_screenshot,
        *record.assets.extra_attachments,
        *record.assets.downloaded_images,
    ):
        if path is None:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            # 人工资产在断点里只存安全文件名，相对 manual_assets/ 解析。
            candidate = manual_dir / candidate.name
        candidates.append(candidate)
    if override is not None:
        for name in (
            override.primary_screenshot_name,
            override.author_screenshot_name,
            *override.attachment_names,
        ):
            if name:
                candidates.append(manual_dir / name)
    candidates.append(job_dir / decision_sidecar_name(record.task.evidence_id))

    deleted: list[Path] = []
    for candidate in candidates:
        resolved = _resolve_inside(job_dir, candidate)
        if resolved is None:
            continue
        # 任务目录可能被外部清理：镜像副本无论本地是否存在都要清理。
        recovery_mirror.discard_mirrored_asset(job_dir.name, resolved.name)
        if not resolved.is_file():
            continue
        try:
            resolved.unlink()
        except OSError as error:
            logger.warning("记录证据文件删除失败 %s：%s", resolved, error)
            continue
        deleted.append(resolved)
    return tuple(deleted)


def _resolve_inside(job_dir: Path, candidate: Path) -> Path | None:
    """解析候选路径；拒绝任务目录之外的任何目标。"""

    try:
        resolved = candidate.resolve()
        resolved.relative_to(job_dir)
    except (OSError, ValueError):
        logger.warning("拒绝删除任务目录外路径：%s", candidate)
        return None
    return resolved


__all__ = ["delete_record_artifacts"]
