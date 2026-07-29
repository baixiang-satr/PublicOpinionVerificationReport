"""Regression tests for platform-level authentication health gating."""

from datetime import datetime
from pathlib import Path

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.crawler.platform_router import PlatformRouter
from src.crawler.platform_scheduler import PlatformTaskScheduler
from src.domain.models import UrlTask


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


def _task() -> UrlTask:
    url = "https://www.zhihu.com/question/362425387"
    return UrlTask(1, url, url)


def _scheduler_with_status(tmp_path: Path, status: AuthStatus) -> PlatformTaskScheduler:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    store.record_result(
        AuthProbeResult(
            platform_key="zhihu",
            status=status,
            checked_at=datetime.now().astimezone(),
            original_url="https://www.zhihu.com/question/362425387",
        )
    )
    return PlatformTaskScheduler(TaskConfig(), PlatformRouter(), store)


def test_challenge_status_does_not_pause_platform(tmp_path: Path) -> None:
    scheduler = _scheduler_with_status(tmp_path, AuthStatus.CHALLENGE)

    assert scheduler.known_auth_block(_task()) is None


def test_expired_status_pauses_platform(tmp_path: Path) -> None:
    scheduler = _scheduler_with_status(tmp_path, AuthStatus.EXPIRED)

    assert scheduler.known_auth_block(_task()) is not None


def test_blocked_auth_platform_exposes_platform_key(tmp_path: Path) -> None:
    scheduler = _scheduler_with_status(tmp_path, AuthStatus.EXPIRED)

    assert scheduler.blocked_auth_platform(_task()) == "zhihu"


def test_blocked_auth_platform_is_none_for_guest_ok(tmp_path: Path) -> None:
    scheduler = _scheduler_with_status(tmp_path, AuthStatus.GUEST_OK)

    assert scheduler.blocked_auth_platform(_task()) is None
