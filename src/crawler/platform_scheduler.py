"""Platform-level task queues and authentication health gating."""

from __future__ import annotations

from urllib.parse import urlsplit

from src.auth.models import AuthProfile, AuthStatus, PlatformAuthPolicy
from src.auth.registry import auth_policy_for_url
from src.auth.store import AuthProfileStore, AuthStateStoreError
from src.config.settings import TaskConfig
from src.crawler.platform_router import PlatformRouter
from src.domain.models import (
    PageData,
    RecordResult,
    RecordStatus,
    TaskError,
    UrlTask,
)


class PlatformTaskScheduler:
    def __init__(
        self,
        config: TaskConfig,
        router: PlatformRouter,
        auth_store: AuthProfileStore | None,
    ) -> None:
        self._config = config
        self._router = router
        self._auth_store = auth_store

    def queues(self, tasks: list[UrlTask]) -> dict[str, list[UrlTask]]:
        queues: dict[str, list[UrlTask]] = {}
        for task in tasks:
            definition = self._router.definition_for(task.normalized_url)
            host = (urlsplit(task.normalized_url).hostname or "unknown").casefold()
            key = definition.key if definition is not None else f"host:{host}"
            queues.setdefault(key, []).append(task)
        return queues

    def known_auth_block(self, task: UrlTask) -> str | None:
        blocking = self._blocking_profile(task)
        if blocking is None:
            return None
        policy, profile = blocking
        if policy.requires_valid_state:
            return (
                f"{policy.display_name} 截图要求使用已验证登录态；"
                "请先在“管理平台登录态”中完成登录和复验，再开始采集。"
            )
        return (
            f"{policy.display_name}登录态当前为“{profile.status.value}”；"
            "为避免批量产生无效页面，已在访问前暂停该平台。"
        )

    def blocked_auth_platform(self, task: UrlTask) -> str | None:
        """Return the platform key whose stored profile blocks crawling."""

        blocking = self._blocking_profile(task)
        return blocking[0].platform_key if blocking is not None else None

    def _blocking_profile(
        self,
        task: UrlTask,
    ) -> tuple[PlatformAuthPolicy, AuthProfile] | None:
        if not self._config.enable_auth_health_gate:
            return None
        policy = auth_policy_for_url(task.normalized_url)
        if policy is None:
            return None
        if self._auth_store is None:
            if policy.requires_valid_state:
                return policy, AuthProfile(
                    profile_id=f"{policy.platform_key}-primary",
                    platform_key=policy.platform_key,
                    auth_scope=policy.auth_scope,
                )
            return None
        try:
            profile = self._auth_store.profile_for(policy.platform_key)
        except (AuthStateStoreError, KeyError):
            if policy.requires_valid_state:
                return policy, AuthProfile(
                    profile_id=f"{policy.platform_key}-primary",
                    platform_key=policy.platform_key,
                    auth_scope=policy.auth_scope,
                )
            return None
        if policy.requires_valid_state:
            try:
                if not self._auth_store.has_valid_state(policy.platform_key):
                    return policy, profile
            except (AuthStateStoreError, KeyError):
                return policy, profile
        if profile.status not in {
            AuthStatus.AUTH_REQUIRED,
            AuthStatus.EXPIRED,
            AuthStatus.WAITING_USER,
        }:
            # CHALLENGE is deliberately excluded: a risk-control challenge is
            # not an expired login, and pausing the whole platform for it
            # produced PLATFORM_AUTH_PAUSED batches right after a fresh
            # login.  CHALLENGE records still crawl with the saved state.
            return None
        return policy, profile

    def auth_paused_result(
        self,
        task: UrlTask,
        message: str,
    ) -> RecordResult:
        page = PageData(final_url=task.normalized_url)
        return RecordResult(
            task=task,
            status=RecordStatus.NEEDS_REVIEW,
            page=page,
            route=self._router.route(task.normalized_url, page),
            errors=[
                TaskError(
                    "auth_gate",
                    "PLATFORM_AUTH_PAUSED",
                    message,
                    retryable=False,
                )
            ],
        )

    @staticmethod
    def cancelled_result(task: UrlTask) -> RecordResult:
        return RecordResult(
            task=task,
            status=RecordStatus.CANCELLED,
            errors=[
                TaskError(
                    "crawl",
                    "CANCELLED",
                    "任务已取消，未再访问该 URL。",
                    retryable=False,
                )
            ],
        )

    def should_pause_after(self, result: RecordResult) -> bool:
        if not self._config.pause_platform_on_auth_failure:
            return False
        return any(
            error.code
            in {
                "LOGIN_REQUIRED",
                "HTTP_401",
                "CAPTCHA_REQUIRED",
                "ACCESS_CHALLENGE",
            }
            for error in result.errors
        )
