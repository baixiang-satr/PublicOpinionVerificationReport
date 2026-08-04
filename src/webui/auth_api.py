"""登录态补充 js_api 方法（独立模块，避免 bridge.py 超 500 行）。

- ``auth_probe_relevant``：只自动复验本次 URL 文件涉及的平台；
- ``auth_resume_login``：抓取中重登弹窗的用户决策（skip/retry）回传。
"""

from __future__ import annotations

from typing import Protocol

from src.webui.auth_runner import AuthRunner
from src.webui.runner import JobRunner


class _AuthApiHost(Protocol):
    auth: AuthRunner
    jobs: JobRunner


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


__all__ = ["AuthApiMixin"]
