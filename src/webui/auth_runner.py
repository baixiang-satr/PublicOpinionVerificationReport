"""登录态后台执行器：保存态复验、交互登录、本次涉及平台自动复验。

从 ``runner.py`` 拆出以遵守单文件 500 行约束；线程模型与 ``JobRunner``
一致（专属 daemon 线程 + 独立 asyncio 循环），事件经 ``EventSink`` 推回 Vue。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from threading import Event
from typing import Callable

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import AUTH_POLICIES, auth_policy_for_key
from src.auth.service import AuthManagerService
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig, default_auth_store_dir
from src.webui.runner import AsyncThreadJob, EventSink
from src.webui.serialize import auth_platform_payload


class AuthRunner(AsyncThreadJob):
    """Saved-state validation and interactive login orchestration."""

    def __init__(
        self,
        task_config_getter,
        sink: EventSink,
        relevant_keys_getter: Callable[[], set[str]] | None = None,
        probe_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(sink)
        self._task_config_getter = task_config_getter
        self._relevant_keys_getter = relevant_keys_getter
        self._probe_timeout_seconds = probe_timeout_seconds
        self._login_confirmation: Event | None = None
        self._active_platform: str | None = None
        self._active_action: str | None = None

    def store(self) -> AuthProfileStore:
        config: TaskConfig = self._task_config_getter()
        return AuthProfileStore(config.auth_store_dir or default_auth_store_dir())

    def _service(self) -> AuthManagerService:
        config: TaskConfig = self._task_config_getter()
        return AuthManagerService(
            config,
            self.store(),
            legacy_state_path=config.storage_state_path,
        )

    def start(self, action: str, platform_key: str | None = None) -> tuple[bool, str]:
        if self.is_running():
            return False, "登录态操作正在进行中，请稍候。"
        with self._lock:
            if action == "login":
                self._login_confirmation = Event()
            self._active_action = action
            self._active_platform = platform_key
        self._spawn(self._run_action(action, platform_key))
        return True, ""

    def confirm_login(self, platform_key: str) -> tuple[bool, str]:
        with self._lock:
            confirmation = self._login_confirmation
            active_platform = self._active_platform
            active_action = self._active_action
        if (
            confirmation is None
            or active_action != "login"
            or active_platform != platform_key
            or not self.is_running()
        ):
            return False, "该平台当前没有等待确认的登录窗口。"
        confirmation.set()
        return True, "正在检查登录结果，成功后会保存并关闭登录窗口。"

    def cancel_login(self, platform_key: str) -> tuple[bool, str]:
        """取消该平台正在进行中的登录或验证（含卡住的验证）。"""

        with self._lock:
            active_platform = self._active_platform
            active_action = self._active_action
        if active_platform != platform_key or not self.is_running():
            return False, "该平台当前没有正在进行的登录态操作。"
        self.cancel()
        if active_action == "login":
            message = "已取消本次登录；原有登录态不会被覆盖。"
        else:
            message = "已取消本次验证；可点击“验证”重试。"
        self._emit(platform_key, self.store().profile_for(platform_key).status, message)
        return True, message

    async def _run_action(self, action: str, platform_key: str | None) -> None:
        cancel_event = Event()
        with self._lock:
            self._thread_cancel = cancel_event
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        if self._consume_cancel_request():
            # 取消信号先于任务注册到达：直接放弃本次操作。
            if platform_key is not None:
                self._emit(
                    platform_key,
                    self.store().profile_for(platform_key).status,
                    "已取消本次登录态操作。",
                )
            return
        service = self._service()
        try:
            if action == "probe_relevant":
                await self._probe_relevant(service, cancel_event)
                return
            if action == "probe_all":
                results = await service.probe_all_saved(
                    cancel_event=cancel_event,
                    on_progress=self._emit,
                )
                for result in results:
                    self._emit(result.platform_key, result.status, result.message)
                return
            if action == "login_all":
                results = await service.login_all_missing(
                    cancel_event=cancel_event,
                    on_progress=self._emit,
                )
                for result in results:
                    self._emit(result.platform_key, result.status, result.message)
                return
            if platform_key is None:
                raise ValueError("必须选择一个平台。")
            try:
                result = await self._probe_guarded(service, action, platform_key, cancel_event)
                self._emit(result.platform_key, result.status, result.message)
            finally:
                if action == "login":
                    with self._lock:
                        self._login_confirmation = None
        finally:
            with self._lock:
                self._active_action = None
                self._active_platform = None

    async def _probe_relevant(
        self,
        service: AuthManagerService,
        cancel_event: Event,
    ) -> None:
        """Re-validate only the platforms referenced by the current URL file."""

        getter = self._relevant_keys_getter
        keys = getter() if getter is not None else set()
        policies = [policy for policy in AUTH_POLICIES if policy.platform_key in keys]
        for policy in policies:
            if cancel_event.is_set():
                break
            with self._lock:
                self._active_platform = policy.platform_key
            self._emit(
                policy.platform_key,
                AuthStatus.PROBING,
                "正在自动复验本次涉及平台的登录态…",
            )
            result = await self._probe_guarded(
                service, "probe", policy.platform_key, cancel_event
            )
            self._emit(result.platform_key, result.status, result.message)

    async def _probe_guarded(
        self,
        service: AuthManagerService,
        action: str,
        platform_key: str,
        cancel_event: Event,
    ) -> AuthProbeResult:
        """Run one probe with an overall timeout so the UI never spins forever."""

        coroutine = service.probe(
            platform_key,
            use_saved_state=action != "probe_guest",
            interactive=action == "login",
            cancel_event=cancel_event,
            login_confirmation_event=(
                self._login_confirmation if action == "login" else None
            ),
            on_progress=self._emit,
        )
        if action == "login":
            # 交互登录由 manual_intervention_timeout_seconds 兜底，不能被打断。
            return await coroutine
        timeout = self._probe_timeout_seconds
        if timeout is None:
            timeout = max(120.0, float(self._task_config_getter().page_timeout_seconds) * 5.0)
        try:
            return await asyncio.wait_for(coroutine, timeout=timeout)
        except asyncio.TimeoutError:
            policy = auth_policy_for_key(platform_key)
            result = AuthProbeResult(
                platform_key=platform_key,
                status=AuthStatus.ERROR,
                checked_at=datetime.now().astimezone(),
                original_url=policy.probe_url,
                barrier_code="PROBE_TIMEOUT",
                message="验证超时（网络缓慢或平台无响应）；请点击“验证”重试。",
                used_saved_state=True,
            )
            # record_result 不会用超时结果降级仍为 VALID 的档案；
            # UI 与持久档案保持一致，避免“转圈结束又跳回旧状态”的困惑。
            profile = self.store().record_result(result)
            return replace(result, status=profile.status)

    def _emit(self, platform_key: str, status: AuthStatus, message: str) -> None:
        display_name = next(
            (p.display_name for p in AUTH_POLICIES if p.platform_key == platform_key),
            platform_key,
        )
        self._sink.emit(
            "auth",
            auth_platform_payload(platform_key, display_name, status, message),
        )
