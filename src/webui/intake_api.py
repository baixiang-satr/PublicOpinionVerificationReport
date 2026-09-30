"""浏览器上传文件的接收 mixin：B/S 模式下替代原生文件对话框。

C/S（pywebview）时代的 pick_* 方法弹系统对话框拿到路径；B/S 下文件由前端
``<input type=file>`` 经 ``POST /api/upload/{kind}`` 传到服务器暂存目录，
再调用这里的 accept_* 方法走原有处理逻辑（解析 / 导入 / 归档命名）。

宿主类需提供：``self._base_config``、``self._input_platform_keys``、
``self._letter_paths``、``self.jobs``、``self._session()``、``self._open_job()``。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
from typing import Any, Iterable

from src.auth.registry import auth_policy_for_url
from src.input.reader import InputReadError, describe_input, read_url_input
from src.services import recovery_mirror
from src.services.zip_import import TemplateZipImportError, TemplateZipImporter
from src.utils.file_utils import UnsafeFileNameError, require_safe_file_name

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
_SCREENSHOT_SLOTS = {"primary": "content", "author": "author"}
_LETTER_SUFFIXES = {".jpg", ".jpeg"}


class FileIntakeApiMixin:
    """accept_* 方法：参数为服务器上已暂存的文件路径，返回与原 pick_* 相同的载荷。"""

    _base_config: Any = None
    _input_platform_keys: set[str]
    _letter_paths: Any = ()
    jobs: Any = None

    def _session(self) -> Any:  # pragma: no cover - 宿主提供
        raise NotImplementedError

    def _open_job(self, job_dir: Path) -> dict:  # pragma: no cover - 宿主提供
        raise NotImplementedError

    def accept_input_file(self, path: str) -> dict:
        """读取上传的 URL 文件（原 pick_input_file 对话框之后的逻辑）。"""

        file_path = Path(path)
        try:
            result = read_url_input(file_path)
        except InputReadError as error:
            return {"path": str(file_path), "url_count": 0, "rejected_count": 0, "error": str(error)}
        self._input_platform_keys = {
            policy.platform_key
            for task in result.tasks
            if (policy := auth_policy_for_url(task.normalized_url)) is not None
        }
        return describe_input(result)

    def accept_zip_file(self, path: str) -> dict:
        """导入上传的 template.zip 交付包（原 pick_zip_file 对话框之后的逻辑）。"""

        importer = TemplateZipImporter(Path(self._base_config.template.output_dir))
        try:
            job_dir = importer.import_zip(Path(path))
        except TemplateZipImportError as error:
            return {"ok": False, "message": str(error)}
        return self._open_job(job_dir)

    def accept_letter_files(self, paths: Iterable[str]) -> dict:
        """登记上传的函文档图片（原 pick_letter_file 对话框之后的逻辑）。"""

        names: list[str] = []
        kept: list[Path] = []
        for raw in paths:
            file_path = Path(raw)
            if file_path.suffix.lower() not in _LETTER_SUFFIXES:
                return {
                    "ok": False,
                    "names": [],
                    "message": f"函文档必须是 .jpg 图片，已拒绝：{file_path.name}",
                }
            try:
                name = require_safe_file_name(file_path.name)
            except UnsafeFileNameError as error:
                return {"ok": False, "names": [], "message": str(error)}
            if name not in names:
                names.append(name)
                kept.append(file_path)
        self._letter_paths = tuple(kept)
        return {"ok": True, "names": names, "message": ""}

    def accept_screenshot(self, evidence_id: int, mode: str, path: str) -> dict:
        """把上传的截图归档到人工资产目录（原 pick_screenshot 对话框之后的逻辑）。"""

        session = self._session()
        if session is None:
            return {"ok": False, "name": ""}
        file_path = Path(path)
        if file_path.suffix.lower() not in _IMAGE_SUFFIXES:
            return {"ok": False, "name": ""}
        eid = int(evidence_id)
        assets_dir = session.manual_assets_dir()
        assets_dir.mkdir(parents=True, exist_ok=True)
        name = screenshot_asset_name(assets_dir, eid, mode, file_path.suffix.lower())
        shutil.copy2(file_path, assets_dir / name)
        recovery_mirror.mirror_file(
            session.job_dir.name,
            assets_dir / name,
            subdir=recovery_mirror.ASSETS_DIR_NAME,
        )
        if mode == "primary":
            session.set_primary_screenshot(eid, name)
        elif mode == "author":
            session.set_author_screenshot(eid, name)
        else:
            override = session.get_override(eid)
            names = list(override.attachment_names) if override else []
            if name not in names:
                names.append(name)
            session.set_attachments(eid, names)
        return {"ok": True, "name": name}


def screenshot_asset_name(
    assets_dir: Path,
    evidence_id: int,
    mode: str,
    suffix: str,
) -> str:
    """标准化人工截图命名：与框选截图同一套规则。

    - 内容页 / 个人页槽位：``001_content.jpg`` / ``001_author.png``，
      同一槽位重复上传直接覆盖（不同后缀的旧文件一并清理）；
    - 附件槽位可多张：``001_attachment_20260730_153000.png``，同秒冲突加序号。
    """

    slot = _SCREENSHOT_SLOTS.get(mode)
    if slot is not None:
        for stale in assets_dir.glob(f"{evidence_id:03d}_{slot}.*"):
            if stale.suffix.lower() != suffix:
                stale.unlink(missing_ok=True)
        return require_safe_file_name(f"{evidence_id:03d}_{slot}{suffix}")
    stem = f"{evidence_id:03d}_attachment_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    candidate = require_safe_file_name(f"{stem}{suffix}")
    counter = 1
    while (assets_dir / candidate).exists():
        counter += 1
        candidate = require_safe_file_name(f"{stem}_{counter}{suffix}")
    return candidate


__all__ = ["FileIntakeApiMixin", "screenshot_asset_name"]
