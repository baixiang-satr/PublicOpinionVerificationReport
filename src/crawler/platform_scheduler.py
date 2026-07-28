"""Platform-level task queues and authentication health gating."""

from __future__ import annotations

from urllib.parse import urlsplit

from src.auth.models import AuthStatus
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
        if not self._config.enable_auth_health_gate or self._auth_store is None:
            return None
        policy = auth_policy_for_url(task.normalized_url)
        if policy is None:
            return None
        try:
            profile = self._auth_store.profile_for(policy.platform_key)
        except (AuthStateStoreError, KeyError):
            return None
        if profile.status not in {
            AuthStatus.AUTH_REQUIRED,
            AuthStatus.CHALLENGE,
            AuthStatus.EXPIRED,
            AuthStatus.WAITING_USER,
        }:
            return None
        return (
            f"{policy.display_name}登录态当前为“{profile.status.value}”；"
            "为避免批量产生无效页面，已在访问前暂停该平台。"
        )

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
