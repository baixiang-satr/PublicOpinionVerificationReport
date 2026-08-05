"""任务恢复镜像：把断点与截图复制到 output/ 之外的持久目录。

output/ 任务目录可能被外部清理（约小时级），应用闪退也会留下半截任务。
镜像落在 LOCALAPPDATA（与登录态/许可证同区），断点或截图缺失时可回退。
默认关闭；应用入口调用 :func:`enable` 后生效，测试用 ``enable(tmp_path)``
隔离。所有镜像操作只告警不抛异常——镜像失败绝不能影响主流程。
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil

from src.config.settings import _local_app_data_root
from src.domain.models import RecordResult
from src.services.manual_assets import MANUAL_ASSETS_DIR_NAME
from src.utils.file_utils import require_safe_file_name

logger = logging.getLogger(__name__)

ASSETS_DIR_NAME = "assets"
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")

_root: Path | None = None


def enable(root: Path | None = None) -> None:
    """启用镜像；``root=None`` 时使用 LOCALAPPDATA 默认根目录。"""

    global _root
    _root = Path(root) if root is not None else _local_app_data_root() / "recovery"


def disable() -> None:
    """关闭镜像（测试隔离用）。"""

    global _root
    _root = None


def mirror_root() -> Path | None:
    return _root


def mirror_file(job_id: str, source: Path | None, *, subdir: str = "") -> Path | None:
    """复制 *source* 到 ``<root>/<job_id>/<subdir>/``；返回镜像路径或 None。"""

    target_dir = _job_dir(job_id)
    if target_dir is None or source is None:
        return None
    source = Path(source)
    if not source.is_file():
        return None
    try:
        name = require_safe_file_name(source.name)
        destination = (target_dir / subdir / name) if subdir else (target_dir / name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (
            destination.is_file()
            and destination.stat().st_mtime >= source.stat().st_mtime
        ):
            return destination
        shutil.copy2(source, destination)
        return destination
    except (OSError, ValueError) as error:
        logger.warning("恢复镜像写入失败 %s：%s", source, error)
        return None


def mirror_record_assets(job_id: str, job_dir: Path, record: RecordResult) -> None:
    """镜像一条记录引用的全部截图；相对文件名按 manual_assets/ 解析。"""

    manual_dir = Path(job_dir) / MANUAL_ASSETS_DIR_NAME
    candidates = [
        record.assets.page_screenshot,
        record.assets.author_screenshot,
        *record.assets.extra_attachments,
    ]
    for path in candidates:
        if path is None:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = manual_dir / candidate.name
        mirror_file(job_id, candidate, subdir=ASSETS_DIR_NAME)


def mirrored_asset(job_id: str, name: str | None) -> Path | None:
    """返回镜像中的截图文件；不存在为 None。"""

    return _mirrored(job_id, name, subdir=ASSETS_DIR_NAME)


def mirrored_json(job_id: str, name: str | None) -> Path | None:
    """返回镜像中的任务级 JSON（断点/补录覆盖）；不存在为 None。"""

    return _mirrored(job_id, name, subdir="")


def _mirrored(job_id: str, name: str | None, *, subdir: str) -> Path | None:
    target_dir = _job_dir(job_id)
    if target_dir is None or not name:
        return None
    try:
        safe = require_safe_file_name(name)
    except ValueError:
        return None
    candidate = (target_dir / subdir / safe) if subdir else (target_dir / safe)
    return candidate if candidate.is_file() else None


def _job_dir(job_id: str) -> Path | None:
    if _root is None or not job_id or not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    return _root / job_id
