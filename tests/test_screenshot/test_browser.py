import json
from datetime import datetime
from pathlib import Path

import pytest

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.screenshot.browser import (
    AuthenticationRequiredError,
    BrowserPool,
    BrowserUnavailableError,
)
from src.screenshot.page_shooter import PageShooter, align_page_for_capture

pytestmark = [pytest.mark.asyncio, pytest.mark.playwright]


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


async def test_browser_pool_forces_visible_mode() -> None:
    pool = BrowserPool(TaskConfig(headless=True))

    assert pool._config.headless is False


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


async def test_cross_site_horizontal_overflow_is_framed_in_real_browser(
    tmp_path: Path,
) -> None:
    config = TaskConfig(
        max_concurrency=1,
        page_timeout_seconds=10,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
        screenshot_format="png",
        full_page_screenshot=True,
    )
    pool = BrowserPool(config)
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page() as page:
            await page.set_content(
                """
                <style>
                  html, body { margin: 0; }
                  .overflow-placeholder { width: 4000px; height: 1px; }
                  article {
                    position: absolute; left: 1200px; top: 80px;
                    width: 640px; height: 500px;
                    background: rgb(220, 40, 40); color: white;
                  }
                </style>
                <div class="overflow-placeholder"></div>
                <article><h1>跨站正文</h1><p>真实内容不能偏出截图。</p></article>
                """
            )

            screenshot = await PageShooter(config).capture(page, 8, tmp_path)

            from PIL import Image

            with Image.open(screenshot) as image:
                assert image.width == config.viewport_width
                red_columns = [
                    x
                    for x in range(image.width)
                    if image.getpixel((x, 120))[:3] == (220, 40, 40)
                ]
            assert red_columns
            assert min(red_columns) <= 180
    finally:
        await pool.close()


async def test_interactive_capture_alignment_moves_profile_into_view() -> None:
    config = TaskConfig(
        max_concurrency=1,
        page_timeout_seconds=10,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
    )
    pool = BrowserPool(config)
    try:
        await pool.start()
    except BrowserUnavailableError as error:
        pytest.skip(str(error))
    try:
        async with pool.page() as page:
            await page.set_content(
                """
                <style>
                  html, body { margin: 0; }
                  .overflow-placeholder { width: 4000px; height: 1px; }
                  .profile-header {
                    position: absolute; left: 1500px; top: 80px;
                    width: 600px; height: 300px;
                    background: #2458a6; color: white;
                  }
                </style>
                <div class="overflow-placeholder"></div>
                <section class="profile-header">
                  <h1>作者主页</h1><p>账号资料与作品列表</p>
                </section>
                """
            )

            aligned = await align_page_for_capture(page)
            geometry = await page.evaluate(
                """() => {
                    const rect = document.querySelector('.profile-header')
                      .getBoundingClientRect();
                    return { left: rect.left, scrollX: window.scrollX };
                }"""
            )

            assert aligned is True
            assert geometry["scrollX"] > 1_000
            assert 100 <= geometry["left"] <= 180
    finally:
        await pool.close()


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


async def test_login_wall_evicts_context_but_preserves_auth_profile(
    tmp_path: Path,
) -> None:
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
            max_concurrency=1,
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
        first_context = None
        async with pool.page(url="https://www.zhihu.com/question/123") as signed_in:
            first_context = signed_in.context
            cookies = await first_context.cookies("https://www.zhihu.com")
            assert any(cookie["value"] == "zhihu-only" for cookie in cookies)
            pool.mark_access_invalid(
                signed_in,
                "https://www.zhihu.com/question/123",
                barrier_code="LOGIN_REQUIRED",
                message="login expired",
            )
            # A single content URL's login wall must not expire the saved
            # profile; only the auth manager probe may do that.
            assert store.profile_for("zhihu").status == AuthStatus.VALID
        async with pool.page(url="https://www.zhihu.com/question/456") as new_page:
            assert new_page.context is not first_context
            restored = await new_page.context.cookies("https://www.zhihu.com")
            assert any(cookie["value"] == "zhihu-only" for cookie in restored)
    finally:
        await pool.close()


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


async def test_platform_states_are_isolated_between_contexts(tmp_path: Path) -> None:
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
    store.commit_validated_state(
        "weibo",
        {
            "cookies": [
                {
                    "name": "session",
                    "value": "weibo-only",
                    "domain": ".weibo.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        },
        AuthProbeResult(
            platform_key="weibo",
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url="https://weibo.com/2/detail/123",
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
        async with pool.page(url="https://weibo.com/2/detail/123") as weibo:
            assert weibo.context is not signed_in_context
            assert await weibo.context.cookies("https://weibo.com")
            assert not await weibo.context.cookies("https://www.zhihu.com")
    finally:
        await pool.close()


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
