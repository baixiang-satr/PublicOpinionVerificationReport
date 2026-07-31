from __future__ import annotations

from typing import Any

from src.config.settings import TaskConfig
from src.screenshot.browser import BrowserPool
from src.screenshot.browser_options import (
    KUAISHOU_MOBILE_USER_AGENT,
    browser_context_options,
)


class FakeAuthStore:
    def load_state(self, _platform_key: str) -> dict[str, Any]:
        return {
            "cookies": [
                {
                    "name": "session",
                    "value": "saved",
                    "domain": ".xiaohongshu.com",
                    "path": "/",
                }
            ],
            "origins": [],
        }


class EmptyAuthStore:
    def load_state(self, _platform_key: str) -> None:
        return None


def test_xiaohongshu_public_share_prefers_verified_profile() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=FakeAuthStore())  # type: ignore[arg-type]

    key, platform_key, source, state = pool._context_spec(
        "https://www.xiaohongshu.com/explore/abc123"
        "?xsec_source=app_share&share_channel=wechat"
    )

    assert key == "profile:xiaohongshu"
    assert platform_key == "xiaohongshu"
    assert source == "profile"
    assert state is not None


def test_xiaohongshu_public_share_uses_guest_without_verified_profile() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=EmptyAuthStore())  # type: ignore[arg-type]

    key, platform_key, source, state = pool._context_spec(
        "https://www.xiaohongshu.com/explore/abc123"
        "?xsec_source=app_share&share_channel=wechat"
    )

    assert key == "guest:xiaohongshu"
    assert platform_key == "xiaohongshu"
    assert source == "guest"
    assert state is None


def test_xiaohongshu_non_share_url_can_use_saved_profile() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=FakeAuthStore())  # type: ignore[arg-type]

    key, platform_key, source, state = pool._context_spec(
        "https://www.xiaohongshu.com/explore/abc123"
    )

    assert key == "profile:xiaohongshu"
    assert platform_key == "xiaohongshu"
    assert source == "profile"
    assert state is not None


def test_kuaishou_guest_uses_isolated_mobile_context() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=EmptyAuthStore())  # type: ignore[arg-type]

    key, platform_key, source, state = pool._context_spec(
        "https://www.kuaishou.com/short-video/3xev27cpa7jba4i"
    )

    assert key == "guest:kuaishou"
    assert platform_key == "kuaishou"
    assert source == "guest"
    assert state is None

    options = browser_context_options(
        TaskConfig(),
        platform_key=platform_key,
    )
    assert options["user_agent"] == KUAISHOU_MOBILE_USER_AGENT


def test_explicit_user_agent_wins_over_kuaishou_default() -> None:
    options = browser_context_options(
        TaskConfig(user_agent="Custom Browser"),
        platform_key="kuaishou",
    )

    assert options["user_agent"] == "Custom Browser"
