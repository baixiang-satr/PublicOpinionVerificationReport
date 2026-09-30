from __future__ import annotations

from typing import Any

import pytest

from src.config.settings import TaskConfig
from src.screenshot.browser import AuthenticationRequiredError, BrowserPool
from src.screenshot.browser_options import (
    KUAISHOU_MOBILE_USER_AGENT,
    browser_context_options,
    browser_launch_options,
    fixed_window_geometry_args,
)


class FakeAuthStore:
    def load_state(self, _platform_key: str) -> dict[str, Any]:
        return {
            "cookies": [
                {
                    "name": "web_session",
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


def test_xiaohongshu_public_share_rejects_missing_verified_profile() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=EmptyAuthStore())  # type: ignore[arg-type]

    with pytest.raises(AuthenticationRequiredError):
        pool._context_spec(
            "https://www.xiaohongshu.com/explore/abc123"
            "?xsec_source=app_share&share_channel=wechat"
        )


def test_xiaohongshu_non_share_url_can_use_saved_profile() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=FakeAuthStore())  # type: ignore[arg-type]

    key, platform_key, source, state = pool._context_spec(
        "https://www.xiaohongshu.com/explore/abc123"
    )

    assert key == "profile:xiaohongshu"
    assert platform_key == "xiaohongshu"
    assert source == "profile"
    assert state is not None


def test_kuaishou_requires_profile_before_mobile_context() -> None:
    pool = BrowserPool(TaskConfig(), auth_store=EmptyAuthStore())  # type: ignore[arg-type]

    with pytest.raises(AuthenticationRequiredError):
        pool._context_spec(
            "https://www.kuaishou.com/short-video/3xev27cpa7jba4i"
        )

    options = browser_context_options(
        TaskConfig(),
        platform_key="kuaishou",
    )
    assert options["user_agent"] == KUAISHOU_MOBILE_USER_AGENT


def test_explicit_user_agent_wins_over_kuaishou_default() -> None:
    options = browser_context_options(
        TaskConfig(user_agent="Custom Browser"),
        platform_key="kuaishou",
    )

    assert options["user_agent"] == "Custom Browser"


def test_background_crawl_browser_pins_window_geometry_and_crash_bubble() -> None:
    """125%/150% 缩放的机器必须与 100% 机器渲染出同一张证据图。"""

    options = browser_launch_options(
        TaskConfig(headless=False, background_crawl_browser=True)
    )
    args = options["args"]

    assert "--window-position=-32000,-32000" in args
    assert "--window-size=1440,900" in args
    assert "--force-device-scale-factor=1" in args
    assert "--hide-crash-restore-bubble" in args
    assert "--disable-session-crashed-bubble" in args
    assert "--disable-gpu" not in args  # 有头窗口保留视频硬解


def test_visible_window_without_background_mode_keeps_os_scaling() -> None:
    """用户可见窗口（登录等）不强制缩放，避免高 DPI 机器上界面过小。"""

    options = browser_launch_options(
        TaskConfig(headless=False, background_crawl_browser=False)
    )
    args = options["args"]

    assert "--force-device-scale-factor=1" not in args
    assert "--hide-crash-restore-bubble" not in args
    assert not any(arg.startswith("--window-size=") for arg in args)


def test_headless_launch_has_no_window_geometry_args() -> None:
    options = browser_launch_options(
        TaskConfig(headless=True, background_crawl_browser=True)
    )
    args = options["args"]

    assert "--force-device-scale-factor=1" not in args
    assert not any(arg.startswith("--window-position=") for arg in args)


def test_fixed_window_geometry_args_matches_config() -> None:
    assert fixed_window_geometry_args(
        TaskConfig(viewport_width=1280, viewport_height=800)
    ) == (
        "--window-size=1280,800",
        "--force-device-scale-factor=1",
    )
