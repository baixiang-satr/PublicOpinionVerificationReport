"""Legacy combined login-state must not bypass mandatory per-platform profiles.

Split from ``test_browser.py`` to keep every file under the 500-line
release-check limit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.screenshot.browser import AuthenticationRequiredError, BrowserPool

pytestmark = [pytest.mark.asyncio, pytest.mark.playwright]


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


async def test_legacy_combined_state_does_not_bypass_mandatory_profiles(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "login-state.json"
    cookie_defaults = {
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }
    legacy_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        **cookie_defaults,
                        "name": "zhihu_session",
                        "value": "zhihu-only",
                        "domain": ".zhihu.com",
                    },
                    {
                        **cookie_defaults,
                        "name": "weibo_session",
                        "value": "weibo-only",
                        "domain": ".weibo.com",
                    },
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    pool = BrowserPool(
        TaskConfig(
            max_concurrency=2,
            page_timeout_seconds=10,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            storage_state_path=legacy_path,
            auth_store_dir=tmp_path / "auth",
        ),
        auth_store=store,
    )
    with pytest.raises(AuthenticationRequiredError):
        pool._context_spec("https://www.zhihu.com/question/123")
    with pytest.raises(AuthenticationRequiredError):
        pool._context_spec("https://weibo.com/2/detail/123")
