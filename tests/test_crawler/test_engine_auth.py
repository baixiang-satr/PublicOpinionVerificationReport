"""Engine auth-gating tests: pause on auth failure and self-heal revalidation."""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.auth.models import AuthStatus
from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import PageData, RecordStatus, UrlTask


class FakeResponse:
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        self.request = None


class FakePage:
    def __init__(self, status: int, url: str) -> None:
        self._status = status
        self.url = url

    async def goto(self, _url: str, **_options: object) -> FakeResponse:
        return FakeResponse(self._status, self.url)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeBrowserPool:
    def __init__(self, statuses: list[int], final_url: str) -> None:
        self._statuses = statuses
        self._final_url = final_url
        self.started = False
        self.closed = False
        self.page_count = 0

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    @asynccontextmanager
    async def page(self, _cancel_event: object, _url: str | None = None):
        self.page_count += 1
        yield FakePage(self._statuses.pop(0), self._final_url)

    def mark_access_valid(self, _page: FakePage, _url: str) -> None:
        return None

    def mark_access_invalid(
        self,
        _page: FakePage,
        _url: str,
        *,
        barrier_code: str,
        message: str,
    ) -> None:
        return None


class StubParser:
    async def extract(self, _page: FakePage, _definition: object) -> PageData:
        return PageData(
            title="Question",
            content_text="Answer body",
            content_summary="Answer body",
            author_name="Author",
        )


class StubShooter:
    async def capture(
        self,
        _page: FakePage,
        evidence_id: int,
        output_dir: Path,
        _cancel_event: object,
    ) -> Path:
        path = output_dir / f"{evidence_id:03d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpeg")
        return path


class ExpiredAuthStore:
    @staticmethod
    def profile_for(_platform_key: str):
        return type("Profile", (), {"status": AuthStatus.EXPIRED})()


def _zhihu_task(evidence_id: int = 1) -> UrlTask:
    url = f"https://www.zhihu.com/question/{evidence_id}"
    return UrlTask(evidence_id, url, url)


@pytest.mark.asyncio
async def test_auth_failure_pauses_remaining_tasks_for_same_platform(tmp_path: Path) -> None:
    pool = FakeBrowserPool([401], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
        ),
        browser_pool=pool,
        parser=StubParser(),
        shooter=StubShooter(),
    )
    tasks = [_zhihu_task(evidence_id) for evidence_id in (1, 2, 3)]

    results = await engine.run(tasks, tmp_path)

    assert pool.page_count == 1
    assert [result.status for result in results] == [
        RecordStatus.NEEDS_REVIEW,
        RecordStatus.NEEDS_REVIEW,
        RecordStatus.NEEDS_REVIEW,
    ]
    assert [error.code for error in results[1].errors] == ["PLATFORM_AUTH_PAUSED"]
    assert [error.code for error in results[2].errors] == ["PLATFORM_AUTH_PAUSED"]


@pytest.mark.asyncio
async def test_known_expired_auth_state_blocks_platform_before_navigation(
    tmp_path: Path,
) -> None:
    pool = FakeBrowserPool([], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(
            auth_store_dir=tmp_path / "auth",
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
        ),
        browser_pool=pool,
        parser=StubParser(),
        shooter=StubShooter(),
        auth_store=ExpiredAuthStore(),
    )

    results = await engine.run([_zhihu_task()], tmp_path)

    assert pool.page_count == 0
    assert results[0].status == RecordStatus.NEEDS_REVIEW
    assert [error.code for error in results[0].errors] == [
        "PLATFORM_AUTH_PAUSED"
    ]


@pytest.mark.asyncio
async def test_expired_platform_self_heals_when_revalidation_succeeds(
    tmp_path: Path,
) -> None:
    class HealingBrowserPool(FakeBrowserPool):
        healed: list[str] = []

        async def revalidate_platform_profile(self, platform_key: str) -> bool:
            self.healed.append(platform_key)
            return True

    pool = HealingBrowserPool([200], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(
            auth_store_dir=tmp_path / "auth",
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            ocr_enabled=False,
        ),
        browser_pool=pool,
        parser=StubParser(),
        shooter=StubShooter(),
        auth_store=ExpiredAuthStore(),
    )

    [result] = await engine.run([_zhihu_task()], tmp_path)

    # The preserved state proved working, so the platform was not paused and
    # the record crawled normally instead of short-circuiting.
    assert pool.healed == ["zhihu"]
    assert pool.page_count == 1
    assert result.status == RecordStatus.ASSETS_READY
    assert not any(
        error.code == "PLATFORM_AUTH_PAUSED" for error in result.errors
    )


@pytest.mark.asyncio
async def test_expired_platform_stays_paused_when_revalidation_fails(
    tmp_path: Path,
) -> None:
    class UnhealableBrowserPool(FakeBrowserPool):
        async def revalidate_platform_profile(self, _platform_key: str) -> bool:
            return False

    pool = UnhealableBrowserPool([], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(
            auth_store_dir=tmp_path / "auth",
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            ocr_enabled=False,
        ),
        browser_pool=pool,
        parser=StubParser(),
        shooter=StubShooter(),
        auth_store=ExpiredAuthStore(),
    )

    [result] = await engine.run([_zhihu_task()], tmp_path)

    assert pool.page_count == 0
    assert result.status == RecordStatus.NEEDS_REVIEW
    assert [error.code for error in result.errors] == ["PLATFORM_AUTH_PAUSED"]
