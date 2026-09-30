"""业务桥：前端可调用的全部 Python 方法（B/S 下经 FastAPI REST 暴露）。

方法的返回值必须是 JSON 可序列化结构；文件选择由浏览器上传后经
``FileIntakeApiMixin`` 的 accept_* 方法接收暂存路径，不再使用原生对话框。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
from src.webui.auth_api import AuthApiMixin
from src.webui.auth_runner import AuthRunner
from src.webui.image_payload import image_payload
from src.webui.runner import CaptureRunner, EventSink, JobRunner
from src.webui.auth_ui import build_auth_list, missing_auth_platforms
from src.webui.api_mixins import (
    FileIntakeApiMixin,
    InvalidUrlApiMixin,
    LetterApiMixin,
    LlmSettingsApiMixin,
    ManualEntryApiMixin,
    RecheckApiMixin,
    ReviewApiMixin,
)
from src.webui.license_gate import LicenseApiMixin, apply_license_guard, default_license_manager
from src.webui.serialize import session_overview


class WebUIBridge(
    LicenseApiMixin,
    AuthApiMixin,
    FileIntakeApiMixin,
    InvalidUrlApiMixin,
    LetterApiMixin,
    RecheckApiMixin,
    ReviewApiMixin,
    ManualEntryApiMixin,
    LlmSettingsApiMixin,
):
    def __init__(
        self,
        base_config: AppConfig,
        sink: EventSink | None = None,
        *,
        license_manager: LicenseManager | None = None,
    ) -> None:
        self._base_config = base_config
        self._task_config: TaskConfig = base_config.task
        self._sink = sink or EventSink()
        self.license = license_manager if license_manager is not None else default_license_manager()
        self._input_platform_keys: set[str] = set()
        self._letter_paths: tuple[Path, ...] = ()
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

    # ── 任务目录 ──
    def _open_job(self, job_dir: Path) -> dict:
        ok, message = self.jobs.open_session(job_dir)
        return {"ok": ok, "message": message}

    # ── 抓取任务 ──
    def start_crawl(self, input_path: str, dedupe: bool = False) -> dict:
        if not input_path:
            return {"ok": False, "message": "请先选择 URL 文件。"}
        try:
            parsed = read_url_input(Path(input_path), dedupe=dedupe)
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
        request = JobRequest(
            input_path=Path(input_path), dedupe=dedupe, letter_paths=self._letter_paths
        )
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

    def resume_checkpoint(self, reexport_only: bool, input_path: str = "", dedupe: bool = False) -> dict:
        checkpoint = self.jobs.last_checkpoint
        # 当前会话（如上传 zip 补录）所在目录的 checkpoint 优先于缓存的
        # last_checkpoint，避免「仅重新导出」读到其他任务的旧断点。
        session = self.jobs.session
        if session is not None:
            session_checkpoint = Path(session.job_dir) / "job_checkpoint.json"
            if session_checkpoint.is_file():
                checkpoint = str(session_checkpoint)
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
                dedupe=dedupe,
            )
        if reexport_only:
            # 断点重导出也锚定交付目录：最终包复制回断点所在任务目录。
            self.jobs.final_copy_dir = Path(checkpoint_path).parent
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
