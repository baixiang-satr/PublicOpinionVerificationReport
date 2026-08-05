"""pywebview js_api 桥：Vue 前端可调用的全部 Python 方法。

方法的返回值必须是 JSON 可序列化结构；文件选择等原生对话框通过注入的
``window_provider`` 获取当前 pywebview 窗口（测试时注入假窗口）。
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
import shutil
import webbrowser

from src.auth.login_evidence import state_has_authenticated_session
from src.auth.registry import auth_policy_for_url
from src.config.settings import AppConfig, TaskConfig
from src.crawler.author_profile_urls import is_author_profile_url
from src.input.reader import InputReadError, read_url_input
from src.license.manager import LicenseManager
from src.services import job_records, recovery_mirror
from src.services.checkpoint_store import CheckpointStore
from src.services.models import JobRequest
from src.services.review_session import ReviewSession
from src.services.zip_import import TemplateZipImportError, TemplateZipImporter
from src.utils.file_utils import require_safe_file_name
from src.webui.auth_api import AuthApiMixin
from src.webui.auth_runner import AuthRunner
from src.webui.image_payload import image_payload
from src.webui.runner import CaptureRunner, EventSink, JobRunner
from src.webui.auth_ui import build_auth_list, missing_auth_platforms
from src.webui.license_gate import LicenseApiMixin, apply_license_guard, default_license_manager
from src.webui.serialize import (
    row_delta,
    session_overview,
    sheet_payload,
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
_SCREENSHOT_SLOTS = {"primary": "content", "author": "author"}


class WebUIBridge(LicenseApiMixin, AuthApiMixin):
    def __init__(
        self,
        base_config: AppConfig,
        sink: EventSink | None = None,
        *,
        window_provider=None,
        license_manager: LicenseManager | None = None,
    ) -> None:
        self._base_config = base_config
        self._task_config: TaskConfig = base_config.task
        self._sink = sink or EventSink()
        self._window_provider = window_provider
        self.license = license_manager if license_manager is not None else default_license_manager()
        self._input_platform_keys: set[str] = set()
        self.auth = AuthRunner(
            lambda: self._task_config, self._sink, relevant_keys_getter=lambda: self._input_platform_keys
        )
        self.jobs = JobRunner(self._current_config, self._sink, auth_runner=self.auth)
        self.capture = CaptureRunner(lambda: self._task_config, self._sink)
        self.jobs.refresh_latest_checkpoint(base_config.template.output_dir)

    # ── 基础 ──
    def _current_config(self) -> AppConfig:
        return replace(self._base_config, task=self._task_config)

    def _session(self) -> ReviewSession | None:
        return self.jobs.session

    def get_bootstrap(self) -> dict:
        return {
            "options": {
                "max_concurrency": self._task_config.max_concurrency,
                "page_timeout_seconds": self._task_config.page_timeout_seconds,
                "max_retries": self._task_config.max_retries,
                "screenshot_format": self._task_config.screenshot_format,
                "headless": False,
            },
            "has_checkpoint": self.jobs.last_checkpoint is not None,
            "session": session_overview(self._session()),
            "license": self.license_status(),
        }

    def set_options(self, options: dict) -> dict:
        try:
            self._task_config = replace(
                self._task_config,
                max_concurrency=int(options["max_concurrency"]),
                page_timeout_seconds=int(options["page_timeout_seconds"]),
                max_retries=int(options["max_retries"]),
                screenshot_format=str(options["screenshot_format"]),
                # Visible mode is mandatory for every website crawler.
                headless=False,
            )
        except (KeyError, TypeError, ValueError) as error:
            return {"ok": False, "message": f"参数无效：{error}"}
        return {"ok": True}

    # ── 文件对话框 ──
    def _pick_file(self, file_types: tuple[str, ...], *, directory: bool = False) -> Path | None:
        import webview

        window = (
            self._window_provider()
            if self._window_provider
            else webview.windows[0]
        )
        # pywebview expects FileDialog enum/int values (OPEN=10,
        # FOLDER=20). Passing the strings "open"/"folder" falls through every
        # WinForms branch, leaving its internal file_path variable unbound.
        dialog_type = (
            webview.FileDialog.FOLDER
            if directory
            else webview.FileDialog.OPEN
        )
        result = window.create_file_dialog(
            dialog_type,
            file_types=() if directory else file_types,
        )
        if not result:
            return None
        return Path(result[0] if isinstance(result, (list, tuple)) else result)

    def pick_input_file(self) -> dict | None:
        path = self._pick_file(("URL 文件 (*.txt;*.csv;*.xlsx)", "全部文件 (*.*)"))
        if path is None:
            return None
        try:
            result = read_url_input(path)
        except InputReadError as error:
            return {"path": str(path), "url_count": 0, "rejected_count": 0, "error": str(error)}
        self._input_platform_keys = {
            policy.platform_key
            for task in result.tasks
            if (policy := auth_policy_for_url(task.normalized_url)) is not None
        }
        return {
            "path": str(path),
            "url_count": len(result.tasks),
            "rejected_count": len(result.rejected_values),
        }

    def pick_zip_file(self) -> dict:
        path = self._pick_file(("template 交付包 (*.zip)", "全部文件 (*.*)"))
        if path is None:
            return {"ok": False, "message": ""}
        importer = TemplateZipImporter(Path(self._base_config.template.output_dir))
        try:
            job_dir = importer.import_zip(path)
        except TemplateZipImportError as error:
            return {"ok": False, "message": str(error)}
        return self._open_job(job_dir)

    def _open_job(self, job_dir: Path) -> dict:
        ok, message = self.jobs.open_session(job_dir)
        return {"ok": ok, "message": message}

    # ── 抓取任务 ──
    def start_crawl(self, input_path: str) -> dict:
        if not input_path:
            return {"ok": False, "message": "请先选择 URL 文件。"}
        try:
            parsed = read_url_input(Path(input_path))
        except InputReadError as error:
            return {"ok": False, "message": f"无法读取 URL 文件：{error}"}
        missing = missing_auth_platforms(
            self._task_config,
            self.auth.store(),
            parsed.tasks,
        )
        if missing:
            names = "、".join(missing)
            return {
                "ok": False,
                "message": (
                    f"开始前登录态检查未通过：{names}。"
                    "请在“管理平台登录态”中只点击对应平台的“登录 / 更新”；"
                    "成功保存一次后，后续抓取会自动复用。"
                ),
            }
        request = JobRequest(input_path=Path(input_path))
        ok, message = self.jobs.start(request)
        return {"ok": ok, "message": message}

    def cancel_job(self) -> dict:
        self.jobs.cancel()
        return {"ok": True}

    def retry_failed(self) -> dict:
        result = self.jobs.result
        if result is None or not result.retryable_tasks:
            return {"ok": False, "message": "没有可重试的失败项。"}
        request = JobRequest(
            tasks=result.retryable_tasks,
            retained_records=tuple(
                record for record in result.records if record.status.value == "exported"
            ),
            label="失败项重试",
        )
        ok, message = self.jobs.start(request)
        return {"ok": ok, "message": message}

    def resume_checkpoint(self, reexport_only: bool, input_path: str = "") -> dict:
        checkpoint = self.jobs.last_checkpoint
        if not checkpoint:
            return {"ok": False, "message": "没有可用的断点。"}
        checkpoint_path = Path(checkpoint)
        if reexport_only:
            snapshot = CheckpointStore.load(checkpoint_path)
            tasks = tuple(record.task for record in snapshot.records)
            if not tasks:
                return {"ok": False, "message": "断点中没有任何记录。"}
            request = JobRequest(
                tasks=tasks,
                resume_checkpoint_path=checkpoint_path,
                reexport_only=True,
                label="仅重新导出",
            )
        else:
            if not input_path:
                return {"ok": False, "message": "断点继续需要先在第 1 步选择原始 URL 文件。"}
            request = JobRequest(
                input_path=Path(input_path),
                resume_checkpoint_path=checkpoint_path,
                label="断点继续",
            )
        ok, message = self.jobs.start(request)
        return {"ok": ok, "message": message}

    def export_zip(self) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False, "message": "还没有可导出的内容。"}
        # 断点文件可能被外部清理或闪退打断：优先从恢复镜像还原，
        # 否则用当前会话的内存记录重建，保证补录成果始终可导出。
        checkpoint = job_records.ensure_checkpoint(session.job_dir, session.records())
        snapshot = CheckpointStore.load(checkpoint)
        tasks = tuple(record.task for record in snapshot.records)
        if not tasks:
            return {"ok": False, "message": "断点中没有任何记录。"}
        request = JobRequest(
            tasks=tasks,
            resume_checkpoint_path=checkpoint,
            reexport_only=True,
            label="人工补录导出",
        )
        # 补录导出的最终 ZIP 复制回原任务目录 template_final.zip（双版本）。
        self.jobs.final_copy_dir = Path(session.job_dir)
        ok, message = self.jobs.start(request)
        return {"ok": ok, "message": message or "导出任务已开始。"}

    # ── 表格数据与人工补录 ──
    def get_sheet_payload(self) -> list[dict]:
        session = self._session()
        if session is None:
            return []
        return sheet_payload(session)

    def apply_edit(self, evidence_id: int, field: str, value: str) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False}
        session.set_field(int(evidence_id), str(field), str(value))
        return {"ok": True, "row": row_delta(session, int(evidence_id))}

    def add_manual_row(self, sheet_name: str) -> dict:
        session = self._session()
        if session is None:
            return {"eid": None}
        try:
            record = session.add_manual_record(str(sheet_name))
        except KeyError:
            return {"eid": None}
        self._sink.emit("session", {})
        return {"eid": record.task.evidence_id}

    def remove_manual_row(self, evidence_id: int) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False}
        ok = session.remove_manual_record(int(evidence_id))
        if ok:
            self._sink.emit("session", {})
        return {"ok": ok}

    def pick_screenshot(self, evidence_id: int, mode: str) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False, "name": ""}
        path = self._pick_file(("图片文件 (*.png;*.jpg;*.jpeg;*.bmp;*.webp)", "全部文件 (*.*)"))
        if path is None or path.suffix.lower() not in _IMAGE_SUFFIXES:
            return {"ok": False, "name": ""}
        eid = int(evidence_id)
        assets_dir = session.manual_assets_dir()
        assets_dir.mkdir(parents=True, exist_ok=True)
        name = _screenshot_asset_name(assets_dir, eid, mode, path.suffix.lower())
        shutil.copy2(path, assets_dir / name)
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

    def list_screenshots(self, evidence_id: int) -> dict:
        """内容页/个人页两张截图的预览载荷；缺失的槽位为 None。"""

        session = self._session()
        if session is None:
            return {"content": None, "author": None}
        try:
            record = session.get_record(int(evidence_id))
        except KeyError:
            return {"content": None, "author": None}
        return {
            "content": image_payload(session.content_screenshot_path(record)),
            "author": image_payload(session.author_screenshot_path(record)),
        }

    def start_region_capture(self, evidence_id: int, target: str) -> dict:
        """打开交互式截图窗口（全屏冻结框选，含浏览器地址栏）。"""

        session = self._session()
        if session is None:
            return {"ok": False, "code": "no_session", "message": "还没有打开的任务。"}
        if target not in ("content", "author"):
            return {"ok": False, "code": "bad_target", "message": "未知截图目标。"}
        try:
            record = session.get_record(int(evidence_id))
        except KeyError:
            return {"ok": False, "code": "no_record", "message": "找不到该记录。"}
        url = (record.page.final_url or record.task.original_url or "").strip()
        if target == "author":
            # 有已核验的作者主页 URL 时直达个人页，避免用户在窗口里手动
            # 跳转（SPA 路由不再需要工具条跨页跟随）。
            author_url = (record.page.author_url or "").strip()
            if author_url.startswith(("http://", "https://")) and is_author_profile_url(
                author_url,
                record.page.final_url,
            ):
                url = author_url
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "code": "no_url", "message": "该行没有可打开的链接。"}
        eid = int(evidence_id)
        policy = auth_policy_for_url(url)
        storage_state = self._capture_storage_state(url)
        if policy is not None and storage_state is None:
            return {
                "ok": False,
                "code": "auth_required",
                "message": (
                    f"{policy.display_name} 没有可用的已验证登录态。"
                    "请先在“管理平台登录态”中执行“登录 / 更新”。"
                ),
            }
        assets_dir = session.manual_assets_dir()

        def _on_saved(name: str, *, _eid: int = eid, _target: str = target) -> None:
            if _target == "content":
                session.set_primary_screenshot(_eid, name)
            else:
                session.set_author_screenshot(_eid, name)
            recovery_mirror.mirror_file(
                session.job_dir.name,
                assets_dir / name,
                subdir=recovery_mirror.ASSETS_DIR_NAME,
            )
            self._sink.emit("session", {})

        ok, message = self.capture.start(
            url=url,
            evidence_id=eid,
            target=target,
            platform_key=policy.platform_key if policy is not None else None,
            storage_state=storage_state,
            assets_dir=assets_dir,
            on_saved=_on_saved,
            focus_texts=(
                tuple(
                    value
                    for value in (
                        record.page.author_name,
                        record.page.author_id,
                    )
                    if value
                )
                if target == "author"
                else ()
            ),
        )
        return {"ok": ok, "message": message}

    def _capture_storage_state(self, url: str) -> dict | None:
        policy = auth_policy_for_url(url)
        if policy is None:
            return None
        try:
            state = self.auth.store().load_state(
                policy.platform_key,
            )
            if (
                state is not None
                and state_has_authenticated_session(policy.platform_key, state) is False
            ):
                return None
            return state
        except Exception:  # noqa: BLE001 — 登录态不可用时拒绝游客截图
            return None

    # ── 系统动作 ──
    def open_url(self, url: str) -> dict:
        url = str(url)
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)
        return {"ok": True}

    def open_output_dir(self) -> dict:
        target: Path | None = None
        result = self.jobs.result
        if result is not None and result.archive_path is not None:
            target = Path(result.archive_path).parent
        if target is None:
            target = Path(self._base_config.template.output_dir)
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))  # type: ignore[attr-defined]  # Windows
        return {"ok": True}

    # ── 登录态 ──
    def auth_list(self) -> list[dict]:
        return build_auth_list(self.auth.store(), self._input_platform_keys)

    def auth_probe_all(self) -> dict:
        return {"ok": self.auth.start("probe_all")[0]}

    def auth_login_all(self) -> dict:
        return {"ok": False, "message": "批量弹出登录页已停用，请逐个平台点击“登录 / 更新”。"}

    def auth_probe(self, platform_key: str) -> dict:
        ok, message = self.auth.start("probe", str(platform_key))
        return {"ok": ok, "message": message}

    def auth_login(self, platform_key: str) -> dict:
        ok, message = self.auth.start("login", str(platform_key))
        return {"ok": ok, "message": message}

    def auth_confirm(self, platform_key: str) -> dict:
        ok, message = self.auth.confirm_login(str(platform_key))
        return {"ok": ok, "message": message}

    def auth_cancel(self, platform_key: str) -> dict:
        ok, message = self.auth.cancel_login(str(platform_key))
        return {"ok": ok, "message": message}

    def auth_logout(self, platform_key: str) -> dict:
        self.auth.store().delete_state(str(platform_key))
        return {"ok": True}


apply_license_guard(WebUIBridge)  # 未激活时拦截业务入口，见 license_gate.py


def _screenshot_asset_name(
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
