"""引擎 LLM 兜底接线测试（全离线：注入假 client/store）。"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.crawler.llm_fallback import LlmFallback
from src.domain.models import PageData, RecordStatus, UrlTask
from src.llm.models import LlmSettings

_CONTENT = "正文内容，作者：张三，发布于 2026年5月1日。"


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
    def __init__(self, status: int, final_url: str) -> None:
        self._status = status
        self._final_url = final_url
        self.page_count = 0

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @asynccontextmanager
    async def page(self, _cancel_event: object, _url: str | None = None):
        self.page_count += 1
        yield FakePage(self._status, self._final_url)

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


class MissingAuthorParser:
    async def extract(self, _page: FakePage, _definition: object) -> PageData:
        return PageData(
            title="已有标题",
            content_text=_CONTENT,
            content_summary=_CONTENT,
            # author_name 缺失 → 触发 LLM 兜底
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


class _FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, _system: str, _user: str) -> str:
        self.calls += 1
        return self.reply


class _Store:
    def __init__(self, settings: LlmSettings) -> None:
        self._settings = settings

    def load(self) -> LlmSettings:
        return self._settings


def _engine(
    tmp_path: Path,
    fallback: LlmFallback,
) -> CrawlEngine:
    return CrawlEngine(
        TaskConfig(
            enable_auth_health_gate=False,
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            ocr_enabled=False,
        ),
        browser_pool=FakeBrowserPool(200, "https://www.zhihu.com/question/1"),
        parser=MissingAuthorParser(),
        shooter=StubShooter(),
        llm_fallback=fallback,
    )


def _complete_settings(enabled: bool) -> LlmSettings:
    return LlmSettings(
        enabled=enabled,
        base_url="https://llm.test/v1",
        model="test-model",
        api_key="sk-test",
        timeout_seconds=5.0,
    )


def _task() -> UrlTask:
    url = "https://www.zhihu.com/question/1"
    return UrlTask(1, url, url)


@pytest.mark.asyncio
async def test_llm_fallback_fills_missing_author(tmp_path: Path) -> None:
    client = _FakeClient('{"author_name": "张三"}')
    fallback = LlmFallback(
        TaskConfig(),
        store=_Store(_complete_settings(True)),
        client_factory=lambda _settings: client,
    )
    engine = _engine(tmp_path, fallback)

    [result] = await engine.run([_task()], tmp_path)

    assert result.status == RecordStatus.ASSETS_READY
    assert result.page.author_name == "张三"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_llm_fallback_skipped_when_disabled(tmp_path: Path) -> None:
    client = _FakeClient('{"author_name": "张三"}')
    fallback = LlmFallback(
        TaskConfig(),
        store=_Store(_complete_settings(False)),
        client_factory=lambda _settings: client,
    )
    engine = _engine(tmp_path, fallback)

    [result] = await engine.run([_task()], tmp_path)

    assert result.page.author_name is None
    assert client.calls == 0


@pytest.mark.asyncio
async def test_llm_fallback_rejects_hallucinated_author(tmp_path: Path) -> None:
    client = _FakeClient('{"author_name": "李四"}')  # 不在原文中 → 丢弃
    fallback = LlmFallback(
        TaskConfig(),
        store=_Store(_complete_settings(True)),
        client_factory=lambda _settings: client,
    )
    engine = _engine(tmp_path, fallback)

    [result] = await engine.run([_task()], tmp_path)

    assert result.page.author_name is None
    assert client.calls == 1


@pytest.mark.asyncio
async def test_llm_fallback_request_failure_degrades_silently(tmp_path: Path) -> None:
    class _FailingClient(_FakeClient):
        async def complete(self, _system: str, _user: str) -> str:
            self.calls += 1
            raise RuntimeError("boom")

    client = _FailingClient("")
    fallback = LlmFallback(
        TaskConfig(),
        store=_Store(_complete_settings(True)),
        client_factory=lambda _settings: client,
    )
    engine = _engine(tmp_path, fallback)

    [result] = await engine.run([_task()], tmp_path)

    assert result.status == RecordStatus.ASSETS_READY  # 主流程不受影响
    assert result.page.author_name is None
    assert client.calls == 1
