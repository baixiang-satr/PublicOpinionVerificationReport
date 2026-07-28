import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.auth.models import AuthStatus
from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import PageData, RecordStatus, UrlTask
from src.screenshot.asset_collector import AssetCollectionResult
from src.screenshot.author_shooter import AuthorScreenshotError


class FakeRequest:
    def __init__(self, url: str, redirected_from: "FakeRequest | None" = None) -> None:
        self.url = url
        self.redirected_from = redirected_from


class FakeResponse:
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.request = FakeRequest(url)


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


class FallbackPage(FakePage):
    def __init__(self, statuses: list[int], url: str) -> None:
        super().__init__(statuses[0], url)
        self._statuses = statuses
        self.visited: list[str] = []

    async def goto(self, url: str, **_options: object) -> FakeResponse:
        self.url = url
        self.visited.append(url)
        return FakeResponse(self._statuses.pop(0), url)


class FallbackBrowserPool(FakeBrowserPool):
    def __init__(self, statuses: list[int], url: str) -> None:
        super().__init__([], url)
        self.fallback_page = FallbackPage(statuses, url)

    @asynccontextmanager
    async def page(self, _cancel_event: object, _url: str | None = None):
        self.page_count += 1
        yield self.fallback_page


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


class AssetParser:
    async def extract(self, _page: FakePage, _definition: object) -> PageData:
        return PageData(
            title="Question",
            content_text="Answer body",
            content_summary="Answer body",
            author_name="Author",
            author_url="https://example.test/author",
            image_urls=["https://example.test/image.png"],
        )


class FailingAuthorShooter:
    async def capture(self, *_args: object) -> Path:
        raise AuthorScreenshotError("AUTHOR_HTTP_ERROR", "作者主页返回 HTTP 403")


class UnexpectedAssetCollector:
    async def collect(
        self,
        _page: object,
        _urls: list[str],
        _evidence_id: int,
        output_dir: Path,
        _cancel_event: object,
    ) -> AssetCollectionResult:
        raise AssertionError(f"正文已经存在，不应下载正文图片：{output_dir}")


@pytest.mark.asyncio
async def test_engine_retries_5xx_records_status_and_closes_browser(tmp_path: Path) -> None:
    pool = FakeBrowserPool([503, 200], "https://www.zhihu.com/question/1")
    config = TaskConfig(
        max_retries=1,
        retry_base_delay_seconds=0,
        min_host_interval_seconds=0,
        page_stabilize_milliseconds=0,
    )
    engine = CrawlEngine(config, browser_pool=pool, parser=StubParser(), shooter=StubShooter())

    [result] = await engine.run(
        [UrlTask(1, "https://www.zhihu.com/question/1", "https://www.zhihu.com/question/1")],
        tmp_path,
    )

    assert result.status == RecordStatus.ASSETS_READY
    assert result.attempt_count == 2
    assert result.page.status_code == 200
    assert result.route is not None and result.route.sheet_name == "微博博客"
    assert [error.code for error in result.errors] == ["HTTP_5XX"]
    assert result.assets.page_screenshot == tmp_path / "001.jpg"
    assert pool.page_count == 2
    assert pool.started and pool.closed


@pytest.mark.asyncio
async def test_engine_honors_pre_set_cancellation_and_still_closes_pool(tmp_path: Path) -> None:
    pool = FakeBrowserPool([200], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(TaskConfig(), browser_pool=pool, parser=StubParser(), shooter=StubShooter())
    cancel_event = asyncio.Event()
    cancel_event.set()

    [result] = await engine.run(
        [UrlTask(1, "https://www.zhihu.com/question/1", "https://www.zhihu.com/question/1")],
        tmp_path,
        cancel_event=cancel_event,
    )

    assert result.status == RecordStatus.CANCELLED
    assert pool.page_count == 0
    assert pool.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "max_retries", "expected_status", "expected_attempts", "error_code"),
    [
        ([401], 2, RecordStatus.NEEDS_REVIEW, 1, "HTTP_401"),
        ([403], 2, RecordStatus.NEEDS_REVIEW, 1, "HTTP_403"),
        ([404], 2, RecordStatus.FAILED, 1, "CONTENT_NOT_FOUND"),
        ([405], 2, RecordStatus.NEEDS_REVIEW, 1, "HTTP_405_ACCESS_RESTRICTED"),
        ([429, 429], 1, RecordStatus.NEEDS_REVIEW, 2, "HTTP_429"),
    ],
)
async def test_engine_applies_http_retry_policy(
    tmp_path: Path,
    statuses: list[int],
    max_retries: int,
    expected_status: RecordStatus,
    expected_attempts: int,
    error_code: str,
) -> None:
    pool = FakeBrowserPool(statuses, "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(
            max_retries=max_retries,
            retry_base_delay_seconds=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
        ),
        browser_pool=pool,
        parser=StubParser(),
        shooter=StubShooter(),
    )

    [result] = await engine.run(
        [UrlTask(1, "https://www.zhihu.com/question/1", "https://www.zhihu.com/question/1")],
        tmp_path,
    )

    assert result.status == expected_status
    assert result.attempt_count == expected_attempts
    assert pool.page_count == expected_attempts
    assert all(error.code == error_code for error in result.errors)


@pytest.mark.asyncio
async def test_only_author_screenshot_is_collected_when_body_text_exists(tmp_path: Path) -> None:
    pool = FakeBrowserPool([200], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(min_host_interval_seconds=0, page_stabilize_milliseconds=0),
        browser_pool=pool,
        parser=AssetParser(),
        shooter=StubShooter(),
        author_shooter=FailingAuthorShooter(),
        asset_collector=UnexpectedAssetCollector(),
    )

    [result] = await engine.run(
        [UrlTask(1, "https://www.zhihu.com/question/1", "https://www.zhihu.com/question/1")],
        tmp_path,
    )

    assert result.status == RecordStatus.ASSETS_READY
    assert result.assets.author_screenshot is None
    assert result.assets.downloaded_images == []
    assert [error.code for error in result.errors] == ["AUTHOR_HTTP_ERROR"]


class PartialParser:
    async def extract(self, _page: FakePage, _definition: object) -> PageData:
        return PageData(title="只有标题")


@pytest.mark.asyncio
async def test_partial_content_is_assets_ready_with_missing_field_warning(tmp_path: Path) -> None:
    pool = FakeBrowserPool([200], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(min_host_interval_seconds=0, page_stabilize_milliseconds=0),
        browser_pool=pool,
        parser=PartialParser(),
        shooter=StubShooter(),
    )

    [result] = await engine.run(
        [UrlTask(1, "https://www.zhihu.com/question/1", "https://www.zhihu.com/question/1")],
        tmp_path,
    )

    assert result.status == RecordStatus.ASSETS_READY
    assert result.assets.page_screenshot == tmp_path / "001.jpg"
    assert "PARTIAL_FIELDS_MISSING" in [error.code for error in result.errors]


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
    tasks = [
        UrlTask(
            evidence_id,
            f"https://www.zhihu.com/question/{evidence_id}",
            f"https://www.zhihu.com/question/{evidence_id}",
        )
        for evidence_id in (1, 2, 3)
    ]

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
    class ExpiredAuthStore:
        @staticmethod
        def profile_for(_platform_key: str):
            return type("Profile", (), {"status": AuthStatus.EXPIRED})()

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

    results = await engine.run(
        [
            UrlTask(
                1,
                "https://www.zhihu.com/question/1",
                "https://www.zhihu.com/question/1",
            )
        ],
        tmp_path,
    )

    assert pool.page_count == 0
    assert results[0].status == RecordStatus.NEEDS_REVIEW
    assert [error.code for error in results[0].errors] == [
        "PLATFORM_AUTH_PAUSED"
    ]


@pytest.mark.asyncio
async def test_known_http_failure_uses_same_platform_official_fallback(
    tmp_path: Path,
) -> None:
    original = "https://m.hupu.com/bbs/641349741.html"
    pool = FallbackBrowserPool([405, 200], original)
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

    [result] = await engine.run([UrlTask(1, original, original)], tmp_path)

    assert result.status == RecordStatus.ASSETS_READY
    assert pool.fallback_page.visited == [
        original,
        "https://bbs.hupu.com/bbs/641349741.html",
    ]
    assert "PLATFORM_FALLBACK_USED" in [
        error.code for error in result.errors
    ]
    assert result.route is not None
    assert result.route.platform_value == "虎扑_虎扑_生活资讯"
