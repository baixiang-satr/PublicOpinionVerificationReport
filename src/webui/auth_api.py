"""登录态补充 js_api 方法（独立模块，避免 bridge.py 超 500 行）。

- ``auth_probe_relevant``：只自动复验本次 URL 文件涉及的平台；
- ``auth_resume_login``：抓取中重登弹窗的用户决策（skip/retry）回传；
- ``auth_import_state``：B/S 服务器部署时的远程登录路径——上传本地浏览器
  导出的 Playwright 登录态 JSON，经账号凭据检测后加密入库（与交互登录
  同一存储），可再点「验证」在线复核。
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Protocol

from src.auth.login_evidence import state_has_authenticated_session
from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import AUTH_POLICIES, auth_policy_for_key
from src.auth.store import AuthStateStoreError
from src.webui.auth_runner import AuthRunner
from src.webui.runner import JobRunner
from src.webui.serialize import auth_platform_payload


class _AuthApiHost(Protocol):
    auth: AuthRunner
    jobs: JobRunner
    _sink: Any


class AuthApiMixin:
    """登录态自动复验与抓取中重登的 js_api 方法，宿主类提供 ``auth``/``jobs``。"""

    def auth_probe_relevant(self: _AuthApiHost) -> dict:
        ok, message = self.auth.start("probe_relevant")
        return {"ok": ok, "message": message}

    def auth_resume_login(self: _AuthApiHost, platform_key: str, action: str) -> dict:
        coordinator = self.jobs.relogin
        if coordinator is None or not self.jobs.is_running():
            return {"ok": False, "message": "当前没有等待处理的登录请求。"}
        ok, message = coordinator.resume(str(platform_key), str(action))
        return {"ok": ok, "message": message}

    def auth_import_state(self: _AuthApiHost, platform_key: str, path: str) -> dict:
        """导入本地浏览器导出的登录态 JSON（服务器无显示环境的登录路径）。"""

        key = str(platform_key)
        try:
            policy = auth_policy_for_key(key)
        except KeyError:
            return {"ok": False, "message": f"未知平台：{key}"}
        try:
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return {"ok": False, "message": f"登录态文件读取失败：{error}"}
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            return {"ok": False, "message": "文件不是有效的登录态（缺少 cookies 列表）。"}
        if state_has_authenticated_session(key, state) is False:
            return {
                "ok": False,
                "message": "导入的登录态只有游客/设备 Cookie，没有检测到账号登录凭据。",
            }
        result = AuthProbeResult(
            platform_key=key,
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url=policy.probe_url,
            message="已从本地浏览器导入登录态；建议点击“验证”在线复核。",
            used_saved_state=True,
        )
        try:
            self.auth.store().commit_validated_state(key, state, result)
        except (AuthStateStoreError, ValueError) as error:
            return {"ok": False, "message": f"登录态保存失败：{error}"}
        if self._sink is not None:
            display_name = next(
                (p.display_name for p in AUTH_POLICIES if p.platform_key == key),
                key,
            )
            self._sink.emit(
                "auth",
                auth_platform_payload(key, display_name, AuthStatus.VALID, result.message),
            )
        return {"ok": True, "message": result.message}


__all__ = ["AuthApiMixin"]
