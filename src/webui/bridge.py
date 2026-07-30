"""pywebview js_api 桥：Vue 前端可调用的全部 Python 方法。

方法的返回值必须是 JSON 可序列化结构；文件选择等原生对话框通过注入的
``window_provider`` 获取当前 pywebview 窗口（测试时注入假窗口）。
"""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import shutil
import webbrowser

from src.auth.registry import AUTH_POLICIES
from src.config.settings import AppConfig, TaskConfig
from src.input.reader import InputReadError, read_url_input
from src.services.checkpoint_store import CheckpointStore
from src.services.models import JobRequest
from src.services.review_session import ReviewSession
from src.services.zip_import import TemplateZipImportError, TemplateZipImporter
from src.utils.file_utils import require_safe_file_name
from src.webui.runner import AuthRunner, EventSink, JobRunner
from src.webui.serialize import (
    auth_profile_payload,
    history_job_payload,
    row_delta,
    session_overview,
    sheet_payload,
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class WebUIBridge:
    def __init__(
        self,
        base_config: AppConfig,
        sink: EventSink | None = None,
        *,
        window_provider=None,
    ) -> None:
        self._base_config = base_config
        self._task_config: TaskConfig = base_config.task
        self._sink = sink or EventSink()
        self._window_provider = window_provider
        self.jobs = JobRunner(self._current_config, self._sink)
        self.auth = AuthRunner(lambda: self._task_config, self._sink)
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
                "headless": self._task_config.headless,
            },
            "has_checkpoint": self.jobs.last_checkpoint is not None,
            "session": session_overview(self._session()),
        }

    def set_options(self, options: dict) -> dict:
        try:
            self._task_config = replace(
                self._task_config,
                max_concurrency=int(options["max_concurrency"]),
                page_timeout_seconds=int(options["page_timeout_seconds"]),
                max_retries=int(options["max_retries"]),
                screenshot_format=str(options["screenshot_format"]),
                headless=bool(options["headless"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            return {"ok": False, "message": f"参数无效：{error}"}
        return {"ok": True}

    # ── 文件对话框 ──
    def _pick_file(self, file_types: tuple[str, ...], *, directory: bool = False) -> Path | None:
        import webview

        window = self._window_provider() if self._window_provider else webview.windows[0]
        if directory:
            result = window.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            result = window.create_file_dialog(webview.OPEN_DIALOG, file_types=file_types)
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

    def pick_job_dir(self) -> dict:
        path = self._pick_file((), directory=True)
        if path is None:
            return {"ok": False, "message": ""}
        return self._open_job(path)

    def _open_job(self, job_dir: Path) -> dict:
        ok, message = self.jobs.open_session(job_dir)
        return {"ok": ok, "message": message}

    def list_history_jobs(self) -> list[dict]:
        output_dir = Path(self._base_config.template.output_dir)
        jobs: list[dict] = []
        for checkpoint in sorted(
            output_dir.glob("*/job_checkpoint.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                snapshot = CheckpointStore.load(checkpoint)
            except Exception:
                continue
            jobs.append(history_job_payload(checkpoint.parent, len(snapshot.records)))
        return jobs[:30]

    # ── 抓取任务 ──
    def start_crawl(self, input_path: str) -> dict:
        if not input_path:
            return {"ok": False, "message": "请先选择 URL 文件。"}
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
        checkpoint = Path(session.job_dir) / "job_checkpoint.json"
        if not checkpoint.is_file():
            return {"ok": False, "message": "找不到任务断点文件。"}
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

    def batch_text_type(self, evidence_ids: list[int], text_type: str) -> dict:
        session = self._session()
        if session is None:
            return {"skipped": 0}
        skipped = session.set_text_type_many([int(e) for e in evidence_ids], str(text_type))
        self._sink.emit("session", {})
        return {"skipped": len(skipped)}

    def copy_from_previous(self, evidence_id: int) -> dict:
        session = self._session()
        if session is None:
            return {"copied": 0}
        previous = session.previous_id(int(evidence_id))
        if previous is None:
            return {"copied": 0}
        copied = session.copy_empty_fields_from(previous, int(evidence_id))
        return {"copied": len(copied)}

    def next_attention(self, evidence_id: int, backwards: bool) -> dict:
        session = self._session()
        if session is None:
            return {"eid": None}
        return {"eid": session.next_attention_id(int(evidence_id), backwards=bool(backwards))}

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
        name = require_safe_file_name(
            f"{eid:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix.lower()}"
        )
        shutil.copy2(path, assets_dir / name)
        if mode == "primary":
            session.set_primary_screenshot(eid, name)
        else:
            override = session.get_override(eid)
            names = list(override.attachment_names) if override else []
            if name not in names:
                names.append(name)
            session.set_attachments(eid, names)
        return {"ok": True, "name": name}

    def screenshot_data_url(self, evidence_id: int) -> dict:
        session = self._session()
        if session is None:
            return {"data_url": None, "name": ""}
        record = session.get_record(int(evidence_id))
        path = session.primary_screenshot_path(record)
        if path is None:
            return {"data_url": None, "name": ""}
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"data_url": f"data:{mime};base64,{encoded}", "name": path.name}

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
        store = self.auth.store()
        return [
            auth_profile_payload(policy.display_name, store.profile_for(policy.platform_key))
            for policy in AUTH_POLICIES
        ]

    def auth_probe_all(self) -> dict:
        ok, _message = self.auth.start("probe_all")
        return {"ok": ok}

    def auth_probe(self, platform_key: str) -> dict:
        ok, _message = self.auth.start("probe", str(platform_key))
        return {"ok": ok}

    def auth_login(self, platform_key: str) -> dict:
        ok, _message = self.auth.start("login", str(platform_key))
        return {"ok": ok}

    def auth_logout(self, platform_key: str) -> dict:
        self.auth.store().delete_state(str(platform_key))
        return {"ok": True}


def dumps_debug(payload) -> str:  # 测试辅助：确认全部载荷可 JSON 序列化
    return json.dumps(payload, ensure_ascii=False)
