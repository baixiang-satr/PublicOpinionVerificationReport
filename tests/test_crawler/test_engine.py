import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import PageData, RecordStatus, TaskError, UrlTask
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
    async def page(self, _cancel_event: object):
        self.page_count += 1
        yield FakePage(self._statuses.pop(0), self._final_url)


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


class StubAssetCollector:
    async def collect(
        self,
        _page: object,
        _urls: list[str],
        _evidence_id: int,
        output_dir: Path,
        _cancel_event: object,
    ) -> AssetCollectionResult:
        path = output_dir / "001_01.png"
        path.write_bytes(b"png")
        return AssetCollectionResult(
            files=(path,),
            errors=(TaskError("image_download", "IMAGE_HTTP_ERROR", "另一张图片失败"),),
        )


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
async def test_optional_asset_failures_do_not_block_valid_record(tmp_path: Path) -> None:
    pool = FakeBrowserPool([200], "https://www.zhihu.com/question/1")
    engine = CrawlEngine(
        TaskConfig(min_host_interval_seconds=0, page_stabilize_milliseconds=0),
        browser_pool=pool,
        parser=AssetParser(),
        shooter=StubShooter(),
        author_shooter=FailingAuthorShooter(),
        asset_collector=StubAssetCollector(),
    )

    [result] = await engine.run(
        [UrlTask(1, "https://www.zhihu.com/question/1", "https://www.zhihu.com/question/1")],
        tmp_path,
    )

    assert result.status == RecordStatus.ASSETS_READY
    assert result.assets.author_screenshot is None
    assert [path.name for path in result.assets.downloaded_images] == ["001_01.png"]
    assert [error.code for error in result.errors] == ["AUTHOR_HTTP_ERROR", "IMAGE_HTTP_ERROR"]
