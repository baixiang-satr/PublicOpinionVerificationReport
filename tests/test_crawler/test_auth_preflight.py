"""抓取前登录态复验：新鲜期（默认 30 分钟）内 VALID 档案跳过复验。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.auth.models import AuthProfile, AuthStatus
from src.config.settings import TaskConfig
from src.crawler.auth_preflight import preflight_auth_profiles
from src.domain.models import UrlTask

_URL = "https://weibo.com/7798269830/5264010640887796"
_PLATFORM = "weibo"


class _FakePool:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls: list[str] = []

    async def revalidate_platform_profile(self, platform_key: str) -> bool:
        self.calls.append(platform_key)
        return self.valid


class _FakeStore:
    def __init__(self, profile: AuthProfile) -> None:
        self._profile = profile

    def profile_for(self, platform_key: str) -> AuthProfile:
        return self._profile


def _profile(status: AuthStatus, validated_at: str | None) -> AuthProfile:
    return AuthProfile(
        profile_id=_PLATFORM,
        platform_key=_PLATFORM,
        auth_scope=_PLATFORM,
        status=status,
        validated_at=validated_at,
    )


def _queues() -> dict[str, list[UrlTask]]:
    task = UrlTask(1, _URL, _URL)
    return {_PLATFORM: [task]}


def _run(
    config: TaskConfig,
    pool: _FakePool,
    store: _FakeStore,
) -> tuple[dict[str, bool], list[str]]:
    messages: list[str] = []

    def emit(record, stage: str, message: str, on_event) -> None:
        messages.append(message)

    result = asyncio.run(
        preflight_auth_profiles(
            config,
            pool,
            store,
            _queues(),
            None,
            emit,
            asyncio.Event(),
        )
    )
    return result, messages


@pytest.mark.parametrize("minutes_ago", [0, 5, 29])
def test_fresh_valid_profile_skips_revalidation(minutes_ago: int) -> None:
    validated_at = (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat()
    pool = _FakePool()
    config = TaskConfig(auth_preflight_freshness_minutes=30)

    result, messages = _run(config, pool, _FakeStore(_profile(AuthStatus.VALID, validated_at)))

    assert result == {_PLATFORM: True}
    assert pool.calls == []  # 新鲜期内不触发复验
    assert any("跳过抓取前复验" in message for message in messages)


def test_stale_valid_profile_still_revalidates() -> None:
    validated_at = (datetime.now().astimezone() - timedelta(minutes=31)).isoformat()
    pool = _FakePool()
    config = TaskConfig(auth_preflight_freshness_minutes=30)

    result, messages = _run(config, pool, _FakeStore(_profile(AuthStatus.VALID, validated_at)))

    assert result == {_PLATFORM: True}
    assert pool.calls == [_PLATFORM]
    assert any("正在抓取前复验" in message for message in messages)


def test_expired_profile_never_skips_revalidation() -> None:
    validated_at = datetime.now().astimezone().isoformat()
    pool = _FakePool(valid=False)
    config = TaskConfig(auth_preflight_freshness_minutes=30)

    result, _ = _run(config, pool, _FakeStore(_profile(AuthStatus.EXPIRED, validated_at)))

    assert result == {_PLATFORM: False}
    assert pool.calls == [_PLATFORM]


def test_missing_or_invalid_timestamp_never_skips() -> None:
    pool = _FakePool()
    config = TaskConfig(auth_preflight_freshness_minutes=30)

    result, _ = _run(config, pool, _FakeStore(_profile(AuthStatus.VALID, "not-a-timestamp")))
    assert result == {_PLATFORM: True}
    assert pool.calls == [_PLATFORM]

    pool2 = _FakePool()
    result2, _ = _run(config, pool2, _FakeStore(_profile(AuthStatus.VALID, None)))
    assert result2 == {_PLATFORM: True}
    assert pool2.calls == [_PLATFORM]


def test_zero_freshness_disables_skip() -> None:
    validated_at = datetime.now().astimezone().isoformat()
    pool = _FakePool()
    config = TaskConfig(auth_preflight_freshness_minutes=0)

    result, _ = _run(config, pool, _FakeStore(_profile(AuthStatus.VALID, validated_at)))

    assert result == {_PLATFORM: True}
    assert pool.calls == [_PLATFORM]


def test_config_carries_freshness_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POIR_AUTH_PREFLIGHT_FRESHNESS_MINUTES", "7")
    from src.config.settings import AppConfig

    config = AppConfig.from_environment()
    assert config.task.auth_preflight_freshness_minutes == 7
    assert replace(config.task).auth_preflight_freshness_minutes == 7
