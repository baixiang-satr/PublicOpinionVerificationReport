"""FastAPI 应用工厂：REST 桥方法 + 浏览器上传/下载 + WS 事件 + 前端静态托管。

路由布局：
- ``POST /api/{method}``：WebUIBridge 公开方法的薄封装（body ``{"args": [...]}``）；
- ``POST /api/upload/{kind}``：浏览器上传（input/zip/letter/screenshot/auth-state），
  暂存后调用桥上的 accept_* / auth_import_state；
- ``GET /api/download/{job-zip,manual-entries}``：交付包与未收录清单下载；
- ``WS /ws/events``：后端事件广播（progress/log/finished/auth/…）；
- ``/``：``web/dist`` 静态托管（未构建时返回提示）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import tempfile
from typing import Any

from fastapi import FastAPI, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config.settings import AppConfig, PROJECT_ROOT
from src.server.events import WebSocketHub
from src.server.uploads import UploadStore
from src.webui.bridge import WebUIBridge
from src.webui.manual_entry_api import MANUAL_ENTRY_FILE_NAME

logger = logging.getLogger(__name__)

# 经 POST /api/{method} 暴露的桥方法白名单（桌面专属/上传类方法不在其中）。
_API_METHODS = (
    "get_bootstrap",
    "set_options",
    "license_status",
    "license_activate",
    "license_deactivate",
    "start_crawl",
    "cancel_job",
    "retry_failed",
    "resume_checkpoint",
    "export_zip",
    "get_sheet_payload",
    "apply_edit",
    "add_manual_row",
    "remove_record",
    "list_screenshots",
    "start_region_capture",
    "list_url_recheck",
    "start_url_recheck",
    "cancel_url_recheck",
    "remove_records",
    "list_invalid_url_candidates",
    "keep_invalid_url_candidates",
    "auth_list",
    "auth_probe_all",
    "auth_probe_relevant",
    "auth_login_all",
    "auth_probe",
    "auth_login",
    "auth_confirm",
    "auth_cancel",
    "auth_logout",
    "auth_resume_login",
    "remove_letter_file",
    "clear_letter_file",
    "letter_state",
    "list_manual_entries",
    "get_llm_settings",
    "save_llm_settings",
    "test_llm_connection",
)
_API_METHOD_SET = frozenset(_API_METHODS)

_FINAL_ARCHIVE_NAME = "template_final.zip"
_INIT_ARCHIVE_NAME = "template.zip"


def _license_block(bridge: WebUIBridge) -> JSONResponse | None:
    """下载类端点的许可证守卫（未激活返回 403 + LICENSE_REQUIRED）。"""

    info = bridge.license.status()
    if info.activated:
        return None
    return JSONResponse(
        status_code=403,
        content={"ok": False, "code": "LICENSE_REQUIRED", "message": info.message},
    )


def create_app(
    *,
    config: AppConfig,
    bridge: WebUIBridge,
    hub: WebSocketHub,
    dist_dir: Path | None = None,
) -> FastAPI:
    uploads = UploadStore(Path(config.template.output_dir) / "_uploads")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        uploads.reset()
        yield

    app = FastAPI(title="网安见微·智舆", lifespan=_lifespan)

    # ── REST 桥方法 ──
    @app.post("/api/{method}")
    async def call_bridge(method: str, request: Request) -> Any:
        if method not in _API_METHOD_SET:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "message": f"未知接口：{method}"},
            )
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        args = body.get("args", []) if isinstance(body, dict) else []
        if not isinstance(args, list):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "请求体必须是 {\"args\": [...]}。"},
            )
        func = getattr(bridge, method)
        try:
            return await run_in_threadpool(lambda: func(*args))
        except Exception as error:  # noqa: BLE001 — 桥方法不应 500 崩掉事件循环
            logger.exception("桥方法 %s 调用失败", method)
            return JSONResponse(
                status_code=500,
                content={"ok": False, "message": f"{type(error).__name__}: {error}"},
            )

    # ── 浏览器上传（暂存后复用桥的 accept_* 逻辑；许可证守卫在桥方法内）──
    @app.post("/api/upload/input")
    async def upload_input(file: UploadFile = File(...)) -> Any:
        path = uploads.save("input", file.filename or "urls.txt", await file.read())
        return await run_in_threadpool(bridge.accept_input_file, str(path))

    @app.post("/api/upload/zip")
    async def upload_zip(file: UploadFile = File(...)) -> Any:
        path = uploads.save("zip", file.filename or "template.zip", await file.read())
        return await run_in_threadpool(bridge.accept_zip_file, str(path))

    @app.post("/api/upload/letter")
    async def upload_letter(files: list[UploadFile] = File(...)) -> Any:
        paths = [
            str(uploads.save("letter", item.filename or "letter.jpg", await item.read()))
            for item in files
        ]
        return await run_in_threadpool(bridge.accept_letter_files, paths)

    @app.post("/api/upload/screenshot")
    async def upload_screenshot(
        evidence_id: int = Query(...),
        mode: str = Query(...),
        file: UploadFile = File(...),
    ) -> Any:
        path = uploads.save("screenshot", file.filename or "shot.png", await file.read())
        return await run_in_threadpool(bridge.accept_screenshot, evidence_id, mode, str(path))

    @app.post("/api/upload/auth-state")
    async def upload_auth_state(
        platform: str = Query(...),
        file: UploadFile = File(...),
    ) -> Any:
        path = uploads.save("auth-state", file.filename or "state.json", await file.read())
        return await run_in_threadpool(bridge.auth_import_state, platform, str(path))

    # ── 下载 ──
    @app.get("/api/download/job-zip")
    def download_job_zip() -> Any:
        blocked = _license_block(bridge)
        if blocked is not None:
            return blocked
        deliver_dir = bridge.jobs.last_deliver_dir
        candidates = (
            [deliver_dir / _FINAL_ARCHIVE_NAME, deliver_dir / _INIT_ARCHIVE_NAME]
            if deliver_dir is not None
            else []
        )
        for archive in candidates:
            if archive.is_file():
                return FileResponse(archive, filename=archive.name)
        return JSONResponse(
            status_code=404,
            content={"ok": False, "message": "还没有可下载的交付包，请先导出。"},
        )

    @app.get("/api/download/manual-entries")
    def download_manual_entries() -> Any:
        blocked = _license_block(bridge)
        if blocked is not None:
            return blocked
        target = Path(tempfile.mkdtemp(prefix="poir-manual-")) / MANUAL_ENTRY_FILE_NAME
        result = bridge.dump_manual_entries_csv(str(target))
        if not result.get("ok"):
            return JSONResponse(status_code=404, content=result)
        return FileResponse(target, filename=MANUAL_ENTRY_FILE_NAME)

    # ── 事件广播 ──
    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(websocket)

    # ── 前端静态托管（最后注册，避免吞掉 API 路由）──
    dist = Path(dist_dir) if dist_dir is not None else PROJECT_ROOT / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:

        @app.get("/")
        def missing_frontend() -> dict:
            return {
                "ok": False,
                "message": "缺少前端构建产物 web/dist，请先在 web/ 目录运行 npm run build。",
            }

    return app


__all__ = ["create_app"]
