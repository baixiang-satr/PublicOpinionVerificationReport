from datetime import datetime
from pathlib import Path

import pytest

import src.auth.service as service_module
from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import auth_policy_for_key
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from tests.test_auth.test_service import (
    FakeBrowser,
    FakeContext,
    FakePage,
    FakeResponse,
    FakeRuntime,
    FakeStarter,
    ReverseProtector,
)


@pytest.mark.asyncio
async def test_interactive_login_opens_selected_login_url_before_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    runtime = FakeRuntime(FakeBrowser(contexts))
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )
    navigated: list[str] = []

    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        navigated.append(url)
        return FakeResponse()

    async def successful_login(*_args: object, **_kwargs: object) -> bool:
        return True

    async def valid_candidate(
        _browser: object,
        _config: TaskConfig,
        platform_key: str,
        _state: dict,
        _cancel: object,
    ) -> AuthProbeResult:
        return AuthProbeResult(
            platform_key=platform_key,
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url=auth_policy_for_key(platform_key).probe_url,
            message="ok",
            used_saved_state=True,
        )

    monkeypatch.setattr(service_module, "_navigate_login", fake_navigate)
    monkeypatch.setattr(service_module, "wait_for_login_evidence", successful_login)
    monkeypatch.setattr(service_module, "validate_candidate", valid_candidate)
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    manager = service_module.AuthManagerService(TaskConfig(), store)
    policy = auth_policy_for_key("xiaohongshu")

    result = await manager.probe(
        policy.platform_key,
        use_saved_state=False,
        interactive=True,
    )

    assert result.status == AuthStatus.VALID
    assert navigated == [policy.login_url]
    assert policy.probe_url not in navigated
    assert runtime.launch_count == 1


@pytest.mark.asyncio
async def test_interactive_login_discards_anonymous_baidu_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    browser = FakeBrowser(contexts)
    runtime = FakeRuntime(browser)
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )

    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def successful_login(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(service_module, "_navigate_login", fake_navigate)
    monkeypatch.setattr(service_module, "wait_for_login_evidence", successful_login)
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    store.commit_validated_state(
        "baijiahao",
        {
            "cookies": [
                {
                    "name": "BAIDUID",
                    "value": "anonymous-device",
                    "domain": ".baidu.com",
                    "path": "/",
                }
            ],
            "origins": [],
        },
        AuthProbeResult(
            platform_key="baijiahao",
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url=auth_policy_for_key("baijiahao").probe_url,
        ),
    )
    manager = service_module.AuthManagerService(TaskConfig(), store)

    result = await manager.probe("baijiahao", interactive=True)

    assert result.status == AuthStatus.VALID
    assert runtime.launch_count == 1
    assert "storage_state" not in browser.context_options[0]


@pytest.mark.asyncio
async def test_interactive_relogin_starts_clean_and_preserves_old_baidu_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    browser = FakeBrowser(contexts)
    runtime = FakeRuntime(browser)
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )

    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def unfinished_login(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(service_module, "_navigate_login", fake_navigate)
    monkeypatch.setattr(service_module, "wait_for_login_evidence", unfinished_login)
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    store.commit_validated_state(
        "baijiahao",
        {
            "cookies": [
                {
                    "name": "BDUSS",
                    "value": "account-session",
                    "domain": ".baidu.com",
                    "path": "/",
                }
            ],
            "origins": [],
        },
        AuthProbeResult(
            platform_key="baijiahao",
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url=auth_policy_for_key("baijiahao").probe_url,
        ),
    )
    manager = service_module.AuthManagerService(TaskConfig(), store)

    result = await manager.probe("baijiahao", interactive=True)

    assert result.status == AuthStatus.VALID
    assert result.used_saved_state is True
    assert "原有有效登录态已保留" in result.message
    assert runtime.launch_count == 1
    assert "storage_state" not in browser.context_options[0]
    assert store.has_valid_state("baijiahao") is True
