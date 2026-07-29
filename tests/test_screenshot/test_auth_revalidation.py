"""Tests for crawl-time auth profile revalidation (self-heal before pausing)."""

from datetime import datetime
from pathlib import Path

import pytest

import src.screenshot.auth_revalidation as revalidation
from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.domain.models import RecordStatus
from src.tools.page_access import AccessBarrier, AccessKind


class ReverseProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


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
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []

    async def new_context(self, **_kwargs: object) -> FakeContext:
        context = FakeContext()
        self.contexts.append(context)
        return context


def _expired_store(tmp_path: Path) -> AuthProfileStore:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    store.commit_validated_state(
        "zhihu",
        {"cookies": [{"name": "s", "value": "x"}], "origins": []},
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
            message="expired",
            used_saved_state=True,
        )
    )
    assert store.profile_for("zhihu").status == AuthStatus.EXPIRED
    return store


@pytest.mark.asyncio
async def test_revalidation_restores_valid_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def no_barrier(_page: FakePage, _final: str, _original: str):
        return None

    monkeypatch.setattr(revalidation, "_navigate", fake_navigate)
    monkeypatch.setattr(revalidation, "inspect_page_access", no_barrier)
    store = _expired_store(tmp_path)

    healed = await revalidation.revalidate_platform_profile(
        FakeBrowser(),
        TaskConfig(),
        store,
        "zhihu",
    )

    assert healed is True
    assert store.profile_for("zhihu").status == AuthStatus.VALID
    assert store.has_valid_state("zhihu")


@pytest.mark.asyncio
async def test_revalidation_confirms_expiry_only_on_login_wall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def login_wall(_page: FakePage, _final: str, _original: str):
        return AccessBarrier(
            AccessKind.LOGIN,
            "LOGIN_REQUIRED",
            "login wall",
            RecordStatus.NEEDS_REVIEW,
            manual_recoverable=True,
        )

    monkeypatch.setattr(revalidation, "_navigate", fake_navigate)
    monkeypatch.setattr(revalidation, "inspect_page_access", login_wall)
    store = _expired_store(tmp_path)

    healed = await revalidation.revalidate_platform_profile(
        FakeBrowser(),
        TaskConfig(),
        store,
        "zhihu",
    )

    assert healed is False
    assert store.profile_for("zhihu").status == AuthStatus.EXPIRED


@pytest.mark.asyncio
async def test_revalidation_dead_probe_pages_do_not_confirm_expiry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_navigate(page: FakePage, url: str, _config: TaskConfig):
        page.url = url
        return FakeResponse()

    async def dead_page(_page: FakePage, _final: str, _original: str):
        return AccessBarrier(
            AccessKind.REDIRECTED_HOME,
            "CONTENT_REDIRECTED_TO_HOME",
            "dead probe page",
            RecordStatus.NEEDS_REVIEW,
        )

    monkeypatch.setattr(revalidation, "_navigate", fake_navigate)
    monkeypatch.setattr(revalidation, "inspect_page_access", dead_page)
    store = _expired_store(tmp_path)

    healed = await revalidation.revalidate_platform_profile(
        FakeBrowser(),
        TaskConfig(),
        store,
        "zhihu",
    )

    # Dead probe pages are inconclusive: not healed, but the profile must not
    # be re-marked EXPIRED either (it keeps the previous marker untouched).
    assert healed is False
    profile = store.profile_for("zhihu")
    assert profile.status == AuthStatus.EXPIRED
    assert profile.state_filename is not None


@pytest.mark.asyncio
async def test_revalidation_without_state_file_cannot_heal(tmp_path: Path) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())

    healed = await revalidation.revalidate_platform_profile(
        FakeBrowser(),
        TaskConfig(),
        store,
        "zhihu",
    )

    assert healed is False
