from datetime import datetime
from pathlib import Path

import pytest

import src.auth.probe_helpers as probe_helpers_module
import src.auth.service as service_module
import src.auth.validation as validation_module
from src.auth.models import (
    AuthProbeResult,
    AuthStatus,
    PlatformAuthPolicy,
)
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

    monkeypatch.setattr(probe_helpers_module, "inspect_page_access", no_barrier)
    monkeypatch.setattr(validation_module, "_navigate", fake_navigate)
    monkeypatch.setattr(validation_module, "inspect_page_access", no_barrier)
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


@pytest.mark.asyncio
async def test_noninteractive_probe_requires_saved_state_even_when_page_is_public(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    runtime = FakeRuntime(FakeBrowser(contexts))
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )

    async def clean_probe(
        page: FakePage,
        policy: PlatformAuthPolicy,
        _config: TaskConfig,
    ):
        page.url = policy.probe_url
        return None, page.url

    monkeypatch.setattr(
        service_module,
        "_navigate_probe_candidates",
        clean_probe,
    )
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    manager = service_module.AuthManagerService(TaskConfig(), store)

    result = await manager.probe("zhihu", use_saved_state=True, interactive=False)

    assert result.status == AuthStatus.AUTH_REQUIRED
    assert result.barrier_code == "LOGIN_STATE_MISSING"
    assert not store.has_valid_state("zhihu")


@pytest.mark.asyncio
async def test_login_all_missing_skips_valid_profiles_and_logs_in_the_rest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policies = AUTH_POLICIES[:2]
    monkeypatch.setattr(service_module, "AUTH_POLICIES", policies)
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    store.commit_validated_state(
        policies[0].platform_key,
        {"cookies": [], "origins": []},
        AuthProbeResult(
            platform_key=policies[0].platform_key,
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url=policies[0].probe_url,
        ),
    )
    manager = service_module.AuthManagerService(TaskConfig(), store)
    called: list[str] = []

    async def fake_probe(
        platform_key: str,
        **_kwargs: object,
    ) -> AuthProbeResult:
        called.append(platform_key)
        return AuthProbeResult(
            platform_key=platform_key,
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url="https://example.test/",
        )

    monkeypatch.setattr(manager, "probe", fake_probe)
    results = await manager.login_all_missing()

    assert len(results) == 2
    assert called == [policies[1].platform_key]
    assert all(result.status == AuthStatus.VALID for result in results)


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

    async def storage_state(self, **_kwargs: object) -> dict:
        return {"cookies": [], "origins": []}

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, contexts: list[FakeContext]) -> None:
        self._contexts = contexts
        self.context_options: list[dict[str, object]] = []

    async def new_context(self, **_kwargs: object) -> FakeContext:
        self.context_options.append(dict(_kwargs))
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


@pytest.mark.asyncio
async def test_revalidation_restores_expired_profile_without_fresh_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    runtime = FakeRuntime(FakeBrowser(contexts))
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )

    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def no_barrier(_page: FakePage, _final: str, _original: str):
        return None

    monkeypatch.setattr(validation_module, "_navigate", fake_navigate)
    monkeypatch.setattr(probe_helpers_module, "inspect_page_access", no_barrier)
    monkeypatch.setattr(validation_module, "inspect_page_access", no_barrier)

    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    state = {
        "cookies": [
            {"name": "session", "value": "x", "domain": ".zhihu.com", "path": "/"}
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
    store.record_result(
        AuthProbeResult(
            platform_key="zhihu",
            status=AuthStatus.EXPIRED,
            checked_at=datetime.now().astimezone(),
            original_url="https://www.zhihu.com/question/362425387",
            barrier_code="LOGIN_REQUIRED",
            message="possibly false-positive expiry",
            used_saved_state=True,
        )
    )
    assert not store.has_valid_state("zhihu")

    manager = service_module.AuthManagerService(TaskConfig(), store)
    result = await manager.probe("zhihu")

    # The preserved cookies still work, so a plain re-validation flips the
    # profile back to VALID without an interactive login.
    assert result.status == AuthStatus.VALID
    profile = store.profile_for("zhihu")
    assert profile.status == AuthStatus.VALID
    assert store.has_valid_state("zhihu")


@pytest.mark.asyncio
async def test_dead_probe_page_falls_back_to_next_candidate(
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

    policy = next(item for item in AUTH_POLICIES if item.platform_key == "zhihu")
    assert len(policy.probe_candidates) >= 2

    async def dead_first_candidate(_page: FakePage, final: str, _original: str):
        if final.rstrip("/") == policy.probe_url.rstrip("/"):
            return AccessBarrier(
                AccessKind.REDIRECTED_HOME,
                "CONTENT_REDIRECTED_TO_HOME",
                "dead probe page",
                RecordStatus.NEEDS_REVIEW,
            )
        return None

    monkeypatch.setattr(validation_module, "_navigate", fake_navigate)
    monkeypatch.setattr(
        probe_helpers_module,
        "inspect_page_access",
        dead_first_candidate,
    )
    monkeypatch.setattr(
        validation_module,
        "inspect_page_access",
        dead_first_candidate,
    )

    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    store.commit_validated_state(
        "zhihu",
        {"cookies": [], "origins": []},
        AuthProbeResult(
            platform_key="zhihu",
            status=AuthStatus.VALID,
            checked_at=datetime.now().astimezone(),
            original_url=policy.probe_url,
        ),
    )
    manager = service_module.AuthManagerService(TaskConfig(), store)

    result = await manager.probe("zhihu")

    # The rotted primary probe page must not be read as an expired login:
    # the fallback candidate renders cleanly and the profile stays VALID.
    assert result.status == AuthStatus.VALID
    assert navigated[0] == policy.probe_url
    assert policy.fallback_probe_urls[0] in navigated
    assert store.has_valid_state("zhihu")


@pytest.mark.asyncio
async def test_interactive_login_commits_state_when_probe_pages_are_dead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contexts: list[FakeContext] = []
    runtime = FakeRuntime(FakeBrowser(contexts))
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakeStarter(runtime),
    )

    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def clean_initial_navigation(page: FakePage, policy: object, _config: TaskConfig):
        # The interactive login page is clean after the user logs in.
        page.url = "https://www.zhihu.com/question/362425387"
        return None, "https://www.zhihu.com/question/362425387"

    async def dead_probe_barrier(_page: FakePage, _final: str, _original: str):
        # Every validation probe page is dead (delisted product etc.).
        return AccessBarrier(
            AccessKind.REDIRECTED_HOME,
            "CONTENT_REDIRECTED_TO_HOME",
            "dead probe page",
            RecordStatus.NEEDS_REVIEW,
        )

    monkeypatch.setattr(validation_module, "_navigate", fake_navigate)
    monkeypatch.setattr(
        service_module,
        "wait_for_login_evidence",
        _successful_login,
    )
    monkeypatch.setattr(
        probe_helpers_module,
        "inspect_page_access",
        dead_probe_barrier,
    )
    monkeypatch.setattr(
        validation_module,
        "inspect_page_access",
        dead_probe_barrier,
    )

    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    manager = service_module.AuthManagerService(TaskConfig(), store)

    result = await manager.probe("zhihu", use_saved_state=False, interactive=True)

    # The user just logged in manually; dead probe pages cannot disprove the
    # fresh cookies, so the state is committed instead of discarded.
    assert result.status == AuthStatus.VALID
    assert "后续抓取将自动加载" in result.message
    assert store.has_valid_state("zhihu")


async def _successful_login(*_args: object, **_kwargs: object) -> bool:
    return True
