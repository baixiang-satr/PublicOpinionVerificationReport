"""Crawl-time interactive re-login coordination (one login window at a time).

The crawl engine calls :meth:`CrawlReloginCoordinator.relogin` on its own
event loop when a platform's saved login state proves expired mid-run.  The
coordinator drives the existing ``AuthRunner`` interactive login (a visible
browser window with 完成登录并保存 / 取消 controls), notifies the Vue side
through ``auth_relogin`` events, and waits until the user either refreshes
the login or explicitly skips the platform.  An ``asyncio.Lock`` serializes
concurrent platform requests so only one login window is ever open.
"""
from __future__ import annotations

import asyncio
from threading import Lock
from typing import Any

from src.auth.models import AuthStatus

_POLL_SECONDS = 0.4
_DECISION_TIMEOUT_SECONDS = 600.0


class CrawlReloginCoordinator:
    """Bridge between the crawl loop and the interactive AuthRunner login."""

    def __init__(self, sink: Any, auth_runner: Any) -> None:
        self._sink = sink
        self._auth = auth_runner
        self._serialize = asyncio.Lock()
        self._flag_lock = Lock()
        self._decisions: dict[str, str] = {}

    def resume(self, platform_key: str, action: str) -> tuple[bool, str]:
        """Record a UI decision (``skip``/``retry``) for a pending platform."""

        if action not in {"skip", "retry"}:
            return False, "未知的操作。"
        with self._flag_lock:
            self._decisions[platform_key] = action
        return True, ""

    def _consume(self, platform_key: str) -> str | None:
        with self._flag_lock:
            return self._decisions.pop(platform_key, None)

    def _emit(self, platform_key: str, display_name: str, phase: str, message: str) -> None:
        self._sink.emit(
            "auth_relogin",
            {
                "key": platform_key,
                "name": display_name,
                "phase": phase,
                "message": message,
            },
        )

    async def _wait_decision(self, platform_key: str, cancel_event: asyncio.Event) -> str | None:
        elapsed = 0.0
        while elapsed < _DECISION_TIMEOUT_SECONDS and not cancel_event.is_set():
            decision = self._consume(platform_key)
            if decision is not None:
                return decision
            await asyncio.sleep(_POLL_SECONDS)
            elapsed += _POLL_SECONDS
        return None

    async def relogin(
        self,
        platform_key: str,
        display_name: str,
        cancel_event: asyncio.Event,
    ) -> bool:
        """Open the platform login window; True once a VALID state is saved."""

        async with self._serialize:
            while not cancel_event.is_set():
                self._consume(platform_key)
                ok, message = self._auth.start("login", platform_key)
                if not ok:
                    self._emit(platform_key, display_name, "failed", message or "无法打开登录窗口。")
                    if await self._wait_decision(platform_key, cancel_event) == "retry":
                        continue
                    return False
                self._emit(
                    platform_key,
                    display_name,
                    "waiting",
                    (
                        f"{display_name} 登录态已失效，已在弹出的浏览器窗口中打开登录页；"
                        "请完成登录后回到本工具点击“完成登录并保存”。"
                    ),
                )
                skipped = False
                while self._auth.is_running():
                    if cancel_event.is_set():
                        self._auth.cancel()
                    if self._consume(platform_key) == "skip":
                        skipped = True
                        self._auth.cancel()
                    await asyncio.sleep(_POLL_SECONDS)
                if cancel_event.is_set():
                    self._emit(platform_key, display_name, "done", "任务已取消，登录流程结束。")
                    return False
                if skipped:
                    self._emit(
                        platform_key, display_name, "done",
                        f"已跳过 {display_name} 的重新登录，该平台剩余 URL 本次不再抓取。",
                    )
                    return False
                profile = self._auth.store().profile_for(platform_key)
                if profile.status == AuthStatus.VALID:
                    self._emit(
                        platform_key, display_name, "done",
                        f"{display_name} 重新登录成功，抓取将继续。",
                    )
                    return True
                self._emit(
                    platform_key,
                    display_name,
                    "failed",
                    "本次未完成登录或复验未通过；可以重试，或暂时跳过该平台。",
                )
                if await self._wait_decision(platform_key, cancel_event) == "retry":
                    continue
                self._emit(platform_key, display_name, "done", f"已跳过 {display_name} 的重新登录。")
                return False
            return False
