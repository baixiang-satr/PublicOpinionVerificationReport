"""Pinned interactive login window configuration and fallback behavior."""

from __future__ import annotations

import pytest

import src.auth.login_window as login_window_module
from src.auth.login_window import open_login_browser
from src.auth.login_window_diagnostics import LoginWindowDiagnostics
from src.config.settings import TaskConfig
from src.screenshot.browser_options import interactive_login_launch_options
from tests.test_auth.test_service import FakeBrowser, FakeContext, FakeRuntime


def test_interactive_login_options_ignore_channel_and_keep_software_rendering() -> (
    None
):
    options = interactive_login_launch_options(TaskConfig(browser_channel="msedge"))

    assert options["headless"] is False
    # The login window is pinned to the bundled Chromium: browser_channel /
    # POR_BROWSER_CHANNEL only steer the crawl and screenshot browsers.
    assert "channel" not in options
    args = options["args"]
    # Software rendering removes the driver-dependent white-flash repaint;
    # login pages never need proprietary video decode.
    assert "--disable-gpu" in args
    # Matches the fixed context device_scale_factor on 125%/150% displays.
    assert "--force-device-scale-factor=1" in args
    assert "--disable-blink-features=AutomationControlled" in args


def test_interactive_login_options_pass_proxy() -> None:
    options = interactive_login_launch_options(
        TaskConfig(proxy_url="http://127.0.0.1:7890")
    )

    assert options["proxy"] == {"server": "http://127.0.0.1:7890"}


@pytest.mark.asyncio
async def test_open_login_browser_stages_offscreen_and_reveals_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[FakeContext] = []
    runtime = FakeRuntime(FakeBrowser(contexts))
    navigated: list[str] = []

    async def fake_navigate(page, url, _config):
        page.url = url
        navigated.append(url)

    bounds: list[str] = []

    async def successful_reveal(_page, *, width: int, height: int) -> bool:
        return True

    async def fake_bounds(_page, *, label: str) -> None:
        bounds.append(label)

    monkeypatch.setattr(login_window_module, "_navigate_login", fake_navigate)
    monkeypatch.setattr(login_window_module, "reveal_window_once", successful_reveal)
    monkeypatch.setattr(login_window_module, "log_window_bounds", fake_bounds)

    _browser, _context, _page = await open_login_browser(
        runtime,
        TaskConfig(),
        "https://example.test/login",
        open_login_trigger=False,
    )

    assert runtime.launch_count == 1
    launch = runtime.launch_kwargs[0]
    assert "channel" not in launch
    args = launch["args"]
    assert "--window-position=-32000,-32000" in args
    assert "--force-device-scale-factor=1" in args
    assert navigated == ["https://example.test/login"]
    assert bounds == ["after-reveal"]
    # Stealth is never injected into the human-operated login window.
    assert contexts[0].init_scripts == []


@pytest.mark.asyncio
async def test_open_login_browser_relaunches_visible_window_when_reveal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[FakeContext] = []
    runtime = FakeRuntime(FakeBrowser(contexts))
    navigated: list[str] = []

    async def fake_navigate(page, url, _config):
        page.url = url
        navigated.append(url)

    async def failed_reveal(_page, *, width: int, height: int) -> bool:
        return False

    monkeypatch.setattr(login_window_module, "_navigate_login", fake_navigate)
    monkeypatch.setattr(login_window_module, "reveal_window_once", failed_reveal)
    monkeypatch.setattr(login_window_module, "_cdp_capable", lambda _context: True)

    _browser, _context, _page = await open_login_browser(
        runtime,
        TaskConfig(),
        "https://example.test/login",
        open_login_trigger=False,
    )

    # A usable visible window beats a RuntimeError that blocks login entirely.
    assert runtime.launch_count == 2
    first, second = runtime.launch_kwargs
    assert "--window-position=-32000,-32000" in first["args"]
    assert "--window-position=-32000,-32000" not in second["args"]
    assert navigated == ["https://example.test/login", "https://example.test/login"]
    assert all(not context.init_scripts for context in contexts)


def test_diagnostics_counts_main_frame_navigations_only() -> None:
    class _RecordingPage:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def on(self, event: str, handler: object) -> None:
            self.handlers[event] = handler

    class _Frame:
        def __init__(self, parent: "_Frame | None") -> None:
            self.parent_frame = parent
            self.url = "https://example.test/"

    class _ConsoleMessage:
        type = "error"

    diagnostics = LoginWindowDiagnostics("wechat_official")
    page = _RecordingPage()
    diagnostics.attach(page)

    top = _Frame(None)
    child = _Frame(top)
    page.handlers["framenavigated"](child)
    page.handlers["framenavigated"](top)
    page.handlers["pageerror"](RuntimeError("boom"))
    page.handlers["console"](_ConsoleMessage())

    assert diagnostics.main_frame_navigations == 1
    assert diagnostics.page_errors == 1
    assert diagnostics.console_errors == 1
