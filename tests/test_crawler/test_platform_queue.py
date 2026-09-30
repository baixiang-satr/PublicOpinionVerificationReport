"""同平台 URL 有界并行调度测试（全离线 Fake）。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.config.settings import AppConfig, TaskConfig
from src.crawler.engine import CrawlEngine
from src.domain.models import PageData, RecordStatus, UrlTask


class FakeResponse:
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        self.request = None


class BlockingPage:
    """按 URL 分流：首条阻塞到 release_first，其余按状态表立即返回。"""

    def __init__(
        self,
        url: str,
        *,
        blocker: tuple[asyncio.Event, asyncio.Event] | None = None,
        status: int = 200,
    ) -> None:
        self.url = url
        self._blocker = blocker
        self._status = status

    async def goto(self, url: str, **_options: object) -> FakeResponse:
        self.url = url
        if self._blocker is not None:
            started, release = self._blocker
            started.set()
            await release.wait()
        return FakeResponse(self._status, url)

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class ParallelPool:
    """按 URL 分流：blocked_url 的页面阻塞到 release，其余按状态表返回。"""

    def __init__(
        self,
        *,
        blocked_url: str | None = None,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        statuses: dict[str, int] | None = None,
    ) -> None:
        self._blocked_url = blocked_url
        self._started = started
        self._release = release
        self._statuses = statuses or {}
        self.started = False
        self.closed = False
        self.page_count = 0

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    @asynccontextmanager
    async def page(self, _cancel_event: object, url: str | None = None):
        self.page_count += 1
        blocker = None
        if url is not None and url == self._blocked_url:
            blocker = (self._started, self._release)
        yield BlockingPage(
            url or "",
            blocker=blocker,
            status=self._statuses.get(url or "", 200),
        )

    def mark_access_valid(self, _page: BlockingPage, _url: str) -> None:
        return None

    def mark_access_invalid(
        self,
        _page: BlockingPage,
        _url: str,
        *,
        barrier_code: str,
        message: str,
    ) -> None:
        return None


class StubParser:
    async def extract(self, _page: BlockingPage, _definition: object) -> PageData:
        return PageData(
            title="Question",
            content_text="Answer body",
            content_summary="Answer body",
            author_name="Author",
        )


class StubShooter:
    async def capture(
        self,
        _page: BlockingPage,
        evidence_id: int,
        output_dir: Path,
        _cancel_event: object,
    ) -> Path:
        path = output_dir / f"{evidence_id:03d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpeg")
        return path


def _zhihu_task(evidence_id: int) -> UrlTask:
    url = f"https://www.zhihu.com/question/{evidence_id}"
    return UrlTask(evidence_id, url, url)


def _engine(pool: ParallelPool, *, concurrency: int) -> CrawlEngine:
    return CrawlEngine(
        TaskConfig(
            enable_auth_health_gate=False,
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            platform_url_concurrency=concurrency,
            ocr_enabled=False,
        ),
        browser_pool=pool,
        parser=StubParser(),
        shooter=StubShooter(),
    )


@pytest.mark.asyncio
async def test_same_platform_urls_crawl_in_parallel(tmp_path: Path) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    pool = ParallelPool(
        blocked_url="https://www.zhihu.com/question/1",
        started=first_started,
        release=release_first,
    )
    engine = _engine(pool, concurrency=2)
    tasks = [_zhihu_task(1), _zhihu_task(2)]

    run_task = asyncio.create_task(engine.run(tasks, tmp_path))
    await asyncio.wait_for(first_started.wait(), timeout=10)
    # 第一条仍阻塞在页面上时，第二条已进入浏览器 → 同平台并行生效。
    await asyncio.wait_for(_wait_for_pages(pool, 2), timeout=10)
    release_first.set()
    results = await asyncio.wait_for(run_task, timeout=30)

    assert pool.page_count == 2
    assert [result.status for result in results] == [
        RecordStatus.ASSETS_READY,
        RecordStatus.ASSETS_READY,
    ]


@pytest.mark.asyncio
async def test_pause_stops_urls_not_yet_started(tmp_path: Path) -> None:
    first_failed = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()
    # 任务1 立即 401；任务2 阻塞（在跑）；任务3/4 排队等信号量。
    pool = ParallelPool(
        blocked_url="https://www.zhihu.com/question/2",
        started=second_started,
        release=release_second,
        statuses={"https://www.zhihu.com/question/1": 401},
    )

    def on_result(record) -> None:
        if record.task.evidence_id == 1:
            first_failed.set()

    engine = _engine(pool, concurrency=2)
    tasks = [_zhihu_task(index) for index in (1, 2, 3, 4)]
    run_task = asyncio.create_task(
        engine.run(tasks, tmp_path, on_result=on_result)
    )
    await asyncio.wait_for(first_failed.wait(), timeout=15)
    # 等 run_one 把 paused_by 置位，再放行任务2 让出信号量。
    await asyncio.sleep(0.2)
    release_second.set()
    results = await asyncio.wait_for(run_task, timeout=30)

    assert pool.page_count == 2  # 任务3/4 暂停，未访问目标站
    assert results[0].status == RecordStatus.NEEDS_REVIEW
    assert results[1].status == RecordStatus.ASSETS_READY
    assert [error.code for error in results[2].errors] == ["PLATFORM_AUTH_PAUSED"]
    assert [error.code for error in results[3].errors] == ["PLATFORM_AUTH_PAUSED"]


@pytest.mark.asyncio
async def test_pre_set_cancellation_cancels_parallel_queue(tmp_path: Path) -> None:
    pool = ParallelPool()
    engine = _engine(pool, concurrency=2)
    cancellation = asyncio.Event()
    cancellation.set()

    results = await engine.run(
        [_zhihu_task(index) for index in (1, 2, 3)],
        tmp_path,
        cancel_event=cancellation,
    )

    assert pool.page_count == 0
    assert [result.status for result in results] == [RecordStatus.CANCELLED] * 3


def test_platform_url_concurrency_validation() -> None:
    with pytest.raises(ValueError):
        TaskConfig(platform_url_concurrency=0)
    with pytest.raises(ValueError):
        TaskConfig(platform_url_concurrency=11)
    assert TaskConfig().platform_url_concurrency == 3


def test_platform_url_concurrency_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("POR_PLATFORM_URL_CONCURRENCY", "5")
    config = AppConfig.from_environment(tmp_path)
    assert config.task.platform_url_concurrency == 5


async def _wait_for_pages(pool: ParallelPool, count: int) -> None:
    while pool.page_count < count:
        await asyncio.sleep(0.02)
