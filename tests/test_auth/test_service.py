from pathlib import Path

import pytest

import src.auth.service as service_module
from src.auth.models import AuthStatus
from src.auth.registry import AUTH_POLICIES
from src.auth.service import _barrier_result, filter_state_for_policy
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.domain.models import RecordStatus
from src.tools.page_access import AccessBarrier, AccessKind


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


def test_legacy_state_is_filtered_to_selected_platform_domains() -> None:
    state = {
        "cookies": [
            {"name": "zhihu", "domain": ".zhihu.com", "value": "keep"},
            {"name": "weibo", "domain": ".weibo.com", "value": "drop"},
        ],
        "origins": [
            {"origin": "https://www.zhihu.com", "localStorage": []},
            {"origin": "https://weibo.com", "localStorage": []},
        ],
    }

    filtered = filter_state_for_policy(state, "zhihu")

    assert [cookie["name"] for cookie in filtered["cookies"]] == ["zhihu"]
    assert [origin["origin"] for origin in filtered["origins"]] == [
        "https://www.zhihu.com"
    ]


def test_dead_url_does_not_become_login_failure() -> None:
    barrier = AccessBarrier(
        AccessKind.REDIRECTED_HOME,
        "CONTENT_REDIRECTED_TO_HOME",
        "redirected",
        RecordStatus.NEEDS_REVIEW,
    )

    result = _barrier_result(
        "zhihu",
        "https://www.zhihu.com/question/123",
        "https://www.zhihu.com/",
        barrier,
        used_saved_state=True,
    )

    assert result.status == AuthStatus.INVALID_URL


def test_login_wall_distinguishes_required_from_expired_state() -> None:
    barrier = AccessBarrier(
        AccessKind.LOGIN,
        "LOGIN_REQUIRED",
        "login",
        RecordStatus.NEEDS_REVIEW,
        manual_recoverable=True,
    )

    guest = _barrier_result("zhihu", "original", "login", barrier, False)
    saved = _barrier_result("zhihu", "original", "login", barrier, True)

    assert guest.status == AuthStatus.AUTH_REQUIRED
    assert saved.status == AuthStatus.EXPIRED


@pytest.mark.asyncio
async def test_probe_all_reuses_browser_but_isolates_guest_contexts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    browser = FakeBrowser(contexts)
    runtime = FakeRuntime(browser)

    monkeypatch.setattr(
        service_module,
        "AUTH_POLICIES",
        AUTH_POLICIES[:2],
    )
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )

    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def no_barrier(_page: FakePage, _final: str, _original: str):
        return None

    monkeypatch.setattr(service_module, "_navigate", fake_navigate)
    monkeypatch.setattr(service_module, "inspect_page_access", no_barrier)
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    manager = service_module.AuthManagerService(TaskConfig(), store)

    results = await manager.probe_all_guest()

    assert runtime.launch_count == 1
    assert len(contexts) == 2
    assert contexts[0] is not contexts[1]
    assert all(context.closed for context in contexts)
    assert [result.status for result in results] == [
        AuthStatus.GUEST_OK,
        AuthStatus.GUEST_OK,
    ]


class FakeResponse:
    status = 200


class FakePage:
    url = "about:blank"


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    async def add_init_script(self, **_kwargs: object) -> None:
        return None

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, contexts: list[FakeContext]) -> None:
        self._contexts = contexts

    async def new_context(self, **_kwargs: object) -> FakeContext:
        context = FakeContext()
        self._contexts.append(context)
        return context

    async def close(self) -> None:
        return None


class FakeChromium:
    def __init__(self, runtime: "FakeRuntime") -> None:
        self._runtime = runtime

    async def launch(self, **_kwargs: object) -> FakeBrowser:
        self._runtime.launch_count += 1
        return self._runtime.browser


class FakeRuntime:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.chromium = FakeChromium(self)
        self.launch_count = 0

    async def stop(self) -> None:
        return None


class FakeStarter:
    def __init__(self, runtime: FakeRuntime) -> None:
        self._runtime = runtime

    async def start(self) -> FakeRuntime:
        return self._runtime
