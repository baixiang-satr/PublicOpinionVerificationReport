from pathlib import Path
from datetime import datetime
import json

import pytest

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.screenshot.browser import BrowserPool, BrowserUnavailableError
from src.screenshot.page_shooter import PageShooter


pytestmark = [pytest.mark.asyncio, pytest.mark.playwright]


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


async def test_owned_browser_context_and_page_are_closed_after_screenshot(tmp_path: Path) -> None:
    config = TaskConfig(
        max_concurrency=1,
        page_timeout_seconds=10,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
        screenshot_format="png",
    )
    pool = BrowserPool(config)
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page() as page:
            await page.set_content("<main><h1>Local fixture</h1><p>Rendered content</p></main>")
            screenshot = await PageShooter(config).capture(page, 7, tmp_path)
            assert screenshot.name == "007.png"
            assert screenshot.read_bytes().startswith(b"\x89PNG")
    finally:
        await pool.close()

    assert not pool.is_started


async def test_pages_share_login_state_and_it_is_reused_by_next_run(tmp_path: Path) -> None:
    state_path = tmp_path / "login-state.json"
    config = TaskConfig(
        max_concurrency=2,
        page_timeout_seconds=10,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
        storage_state_path=state_path,
    )
    first_pool = BrowserPool(config)
    try:
        await first_pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with first_pool.page() as first_page:
            await first_page.context.add_cookies(
                [{"name": "session", "value": "logged-in", "url": "https://example.test"}]
            )
        async with first_pool.page() as second_page:
            cookies = await second_page.context.cookies("https://example.test")
            assert any(cookie["name"] == "session" for cookie in cookies)
    finally:
        await first_pool.close()

    assert state_path.is_file()

    second_pool = BrowserPool(config)
    await second_pool.start()
    try:
        async with second_pool.page() as restored_page:
            cookies = await restored_page.context.cookies("https://example.test")
            assert any(
                cookie["name"] == "session" and cookie["value"] == "logged-in"
                for cookie in cookies
            )
    finally:
        await second_pool.close()


async def test_invalid_login_state_falls_back_to_fresh_session(tmp_path: Path) -> None:
    state_path = tmp_path / "broken-login-state.json"
    state_path.write_text("{not-json", encoding="utf-8")
    pool = BrowserPool(
        TaskConfig(
            max_concurrency=1,
            page_timeout_seconds=10,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            storage_state_path=state_path,
        )
    )
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page() as page:
            await page.set_content("<main>fresh session</main>")
            assert await page.locator("main").inner_text() == "fresh session"
    finally:
        await pool.close()

    assert '"cookies"' in state_path.read_text(encoding="utf-8")


async def test_platform_state_is_not_shared_with_guest_context(tmp_path: Path) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "zhihu-only",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    store.commit_validated_state(
        "zhihu",
        state,
        AuthProbeResult(
            platform_key="zhihu",
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url="https://www.zhihu.com/question/362425387",
        ),
    )
    pool = BrowserPool(
        TaskConfig(
            max_concurrency=2,
            page_timeout_seconds=10,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            auth_store_dir=tmp_path / "auth",
        ),
        auth_store=store,
    )
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page(url="https://www.zhihu.com/question/123") as signed_in:
            signed_in_context = signed_in.context
            cookies = await signed_in_context.cookies("https://www.zhihu.com")
            assert any(cookie["value"] == "zhihu-only" for cookie in cookies)
            pool.mark_access_invalid(
                signed_in,
                "https://www.zhihu.com/question/123",
                barrier_code="CONTENT_REDIRECTED_TO_HOME",
                message="dead content URL",
            )
            assert store.profile_for("zhihu").status == AuthStatus.VALID
        async with pool.page(url="https://weibo.com/2/detail/123") as guest:
            assert guest.context is not signed_in_context
            assert not await guest.context.cookies("https://www.zhihu.com")
    finally:
        await pool.close()


async def test_legacy_combined_state_is_filtered_into_platform_contexts(
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
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page(url="https://www.zhihu.com/question/123") as zhihu:
            zhihu_context = zhihu.context
            assert await zhihu_context.cookies("https://www.zhihu.com")
            assert not await zhihu_context.cookies("https://weibo.com")
        async with pool.page(url="https://weibo.com/2/detail/123") as weibo:
            assert weibo.context is not zhihu_context
            assert await weibo.context.cookies("https://weibo.com")
            assert not await weibo.context.cookies("https://www.zhihu.com")
    finally:
        await pool.close()
