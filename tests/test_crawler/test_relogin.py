"""crawler.relogin 抓取中重登辅助函数测试（全离线）。"""
from __future__ import annotations

import asyncio

from src.crawler.relogin import (
    heal_or_relogin,
    is_login_failure,
    relogin_after_auth_failure,
)
from src.auth.registry import auth_policy_for_key
from src.domain.models import RecordResult, RecordStatus, TaskError, UrlTask

_WEIBO_URL = "https://weibo.com/2/detail/5321665832821282"


def _record(codes: list[str]) -> RecordResult:
    return RecordResult(
        task=UrlTask(1, _WEIBO_URL, _WEIBO_URL),
        status=RecordStatus.NEEDS_REVIEW,
        errors=[TaskError("auth", code, "m", retryable=False) for code in codes],
    )


class _Pool:
    def __init__(self, heal_ok: bool) -> None:
        self._heal_ok = heal_ok
        self.calls: list[str] = []

    async def revalidate_platform_profile(self, platform_key: str) -> bool:
        self.calls.append(platform_key)
        return self._heal_ok


def test_is_login_failure_codes() -> None:
    assert is_login_failure(_record(["LOGIN_REQUIRED"]))
    assert is_login_failure(_record(["HTTP_401"]))
    assert is_login_failure(_record(["PLATFORM_AUTH_PAUSED"]))
    assert not is_login_failure(_record(["CAPTCHA_REQUIRED"]))
    assert not is_login_failure(_record(["ACCESS_CHALLENGE"]))
    assert not is_login_failure(_record([]))


async def test_relogin_after_auth_failure_without_handler() -> None:
    record = _record(["LOGIN_REQUIRED"])
    assert (
        await relogin_after_auth_failure(None, record.task, record, asyncio.Event())
        is False
    )


async def test_relogin_after_auth_failure_ignores_non_login_errors() -> None:
    async def handler(key, name, cancel) -> bool:
        raise AssertionError("非登录失败不应触发重登")

    record = _record(["CAPTCHA_REQUIRED"])
    assert (
        await relogin_after_auth_failure(handler, record.task, record, asyncio.Event())
        is False
    )


async def test_relogin_after_auth_failure_invokes_handler_with_platform() -> None:
    calls: list[tuple[str, str]] = []

    async def handler(key: str, name: str, cancel) -> bool:
        calls.append((key, name))
        return True

    record = _record(["HTTP_401"])
    assert (
        await relogin_after_auth_failure(handler, record.task, record, asyncio.Event())
        is True
    )
    assert calls == [("weibo", auth_policy_for_key("weibo").display_name)]


async def test_relogin_after_auth_failure_unknown_platform() -> None:
    async def handler(key, name, cancel) -> bool:
        raise AssertionError("未知平台不应触发重登")

    record = _record(["LOGIN_REQUIRED"])
    task = UrlTask(2, "https://example.com/x", "https://example.com/x")
    record = RecordResult(
        task=task,
        status=RecordStatus.NEEDS_REVIEW,
        errors=[TaskError("auth", "LOGIN_REQUIRED", "m", retryable=False)],
    )
    assert (
        await relogin_after_auth_failure(handler, task, record, asyncio.Event())
        is False
    )


async def test_heal_or_relogin_preflight_true_short_circuits() -> None:
    async def handler(key, name, cancel) -> bool:
        raise AssertionError("preflight 已通过时不应触发重登")

    pool = _Pool(heal_ok=False)
    assert (
        await heal_or_relogin(handler, pool, "weibo", {"weibo": True}, asyncio.Event())
        is True
    )
    assert pool.calls == []


async def test_heal_or_relogin_heals_without_prompt() -> None:
    async def handler(key, name, cancel) -> bool:
        raise AssertionError("隐藏自愈成功时不应触发重登")

    pool = _Pool(heal_ok=True)
    assert await heal_or_relogin(handler, pool, "weibo", {}, asyncio.Event()) is True
    assert pool.calls == ["weibo"]


async def test_heal_or_relogin_prompts_after_failed_preflight() -> None:
    calls: list[str] = []

    async def handler(key: str, name: str, cancel) -> bool:
        calls.append(key)
        return True

    pool = _Pool(heal_ok=True)
    assert (
        await heal_or_relogin(handler, pool, "weibo", {"weibo": False}, asyncio.Event())
        is True
    )
    # preflight 已失败 → 跳过重复自愈，直接弹登录，成功后再复验一次
    assert calls == ["weibo"]
    assert pool.calls == ["weibo"]


async def test_heal_or_relogin_returns_false_when_user_skips() -> None:
    async def handler(key, name, cancel) -> bool:
        return False

    pool = _Pool(heal_ok=False)
    assert await heal_or_relogin(handler, pool, "weibo", {}, asyncio.Event()) is False
