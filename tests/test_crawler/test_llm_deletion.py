"""引擎 LLM 失效判定接线测试（全离线：注入假 client/store）。

关键路径：
- 规则已确证（HTTP 404 等）→ 直接收集候选，绝不发起 LLM 请求；
- 灰区页面模型编造原文中不存在的句子 → 校验失败 → 不收集候选；
- 灰区页面逐字引用命中原文 → 判 CONTENT_DELETED_LLM 并收集候选。
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.config.settings import TaskConfig
from src.crawler.engine import CrawlEngine
from src.crawler.llm_deletion import LlmDeletionJudge, is_gray_zone_page
from src.domain.models import PageData, RecordStatus, UrlTask
from src.llm.models import LlmSettings

_LONG_CONTENT = "这是一段正常的新闻正文内容。" * 20  # 远超灰区阈值
_DELETED_CONTENT = "提示：该作品已被作者删除，感谢关注。"
_DELETED_QUOTE = "该作品已被作者删除"


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

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @asynccontextmanager
    async def page(self, _cancel_event: object, _url: str | None = None):
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


class StubParser:
    def __init__(self, content: str) -> None:
        self._content = content

    async def extract(self, _page: FakePage, _definition: object) -> PageData:
        return PageData(
            title="已有标题",
            content_text=self._content,
            content_summary=self._content,
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


def _complete_settings(enabled: bool = True) -> LlmSettings:
    return LlmSettings(
        enabled=enabled,
        base_url="https://llm.test/v1",
        model="test-model",
        api_key="sk-test",
        timeout_seconds=5.0,
    )


def _judge(client: _FakeClient, *, enabled: bool = True) -> LlmDeletionJudge:
    return LlmDeletionJudge(
        TaskConfig(),
        store=_Store(_complete_settings(enabled)),
        client_factory=lambda _settings: client,
    )


def _engine(
    tmp_path: Path,
    judge: LlmDeletionJudge,
    *,
    status: int = 200,
    content: str = _DELETED_CONTENT,
) -> CrawlEngine:
    url = "https://www.zhihu.com/question/1"
    return CrawlEngine(
        TaskConfig(
            enable_auth_health_gate=False,
            max_retries=0,
            min_host_interval_seconds=0,
            page_stabilize_milliseconds=0,
            ocr_enabled=False,
        ),
        browser_pool=FakeBrowserPool(status, url),
        parser=StubParser(content),
        shooter=StubShooter(),
        deletion_judge=judge,
    )


def _task() -> UrlTask:
    url = "https://www.zhihu.com/question/1"
    return UrlTask(1, url, url)


def test_gray_zone_gate_blocks_normal_pages() -> None:
    assert is_gray_zone_page(PageData(content_text=_DELETED_CONTENT)) is True
    assert is_gray_zone_page(PageData(content_text=_LONG_CONTENT)) is False
    assert is_gray_zone_page(PageData(content_text="")) is True


@pytest.mark.asyncio
async def test_judge_skipped_when_settings_incomplete(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该作品已被作者删除"}')
    judge = _judge(client, enabled=False)

    quote = await judge.detect_deleted_quote(PageData(content_text=_DELETED_CONTENT), None)

    assert quote is None
    assert client.calls == 0


@pytest.mark.asyncio
async def test_judge_never_called_for_normal_content(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该作品已被作者删除"}')
    judge = _judge(client)

    quote = await judge.detect_deleted_quote(PageData(content_text=_LONG_CONTENT), None)

    assert quote is None
    assert client.calls == 0


@pytest.mark.asyncio
async def test_judge_rejects_fabricated_quote(tmp_path: Path) -> None:
    # 模型编造原文中不存在的句子 → 出处校验失败 → 不采信
    client = _FakeClient('{"deleted_quote": "该内容因违规被平台下架处理"}')
    judge = _judge(client)

    quote = await judge.detect_deleted_quote(PageData(content_text=_DELETED_CONTENT), None)

    assert quote is None
    assert client.calls == 1


@pytest.mark.asyncio
async def test_judge_accepts_verbatim_quote(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该作品已被作者删除"}')
    judge = _judge(client)

    quote = await judge.detect_deleted_quote(PageData(content_text=_DELETED_CONTENT), None)

    assert quote == _DELETED_QUOTE
    assert client.calls == 1


@pytest.mark.asyncio
async def test_judge_honours_cancel_and_silent_failure(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该作品已被作者删除"}')
    cancelled = asyncio.Event()
    cancelled.set()
    quote = await _judge(client).detect_deleted_quote(
        PageData(content_text=_DELETED_CONTENT),
        cancelled,
    )
    assert quote is None
    assert client.calls == 0

    class _FailingClient(_FakeClient):
        async def complete(self, _system: str, _user: str) -> str:
            self.calls += 1
            raise RuntimeError("boom")

    failing = _FailingClient("")
    quote = await _judge(failing).detect_deleted_quote(
        PageData(content_text=_DELETED_CONTENT),
        None,
    )
    assert quote is None  # 静默降级，不抛出
    assert failing.calls == 1


@pytest.mark.asyncio
async def test_engine_rule_confirmed_404_never_calls_llm(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该作品已被作者删除"}')
    engine = _engine(tmp_path, _judge(client), status=404)

    [result] = await engine.run([_task()], tmp_path)

    assert result.status == RecordStatus.FAILED
    assert client.calls == 0  # 规则确证场景不发起 LLM 请求
    [candidate] = engine.invalid_candidates
    assert candidate.evidence_id == 1
    assert candidate.code == "CONTENT_NOT_FOUND"
    assert candidate.citation == "HTTP 404"


@pytest.mark.asyncio
async def test_engine_fabricated_quote_keeps_record_unchanged(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该内容因违规被平台下架处理"}')
    engine = _engine(tmp_path, _judge(client), content=_DELETED_CONTENT)

    [result] = await engine.run([_task()], tmp_path)

    assert client.calls == 1
    assert engine.invalid_candidates == []  # 校验失败 → 不进弹窗
    assert result.status == RecordStatus.ASSETS_READY  # 保持原状态


@pytest.mark.asyncio
async def test_engine_llm_confirmed_deletion_collects_candidate(tmp_path: Path) -> None:
    client = _FakeClient('{"deleted_quote": "该作品已被作者删除"}')
    engine = _engine(tmp_path, _judge(client), content=_DELETED_CONTENT)

    [result] = await engine.run([_task()], tmp_path)

    assert result.status == RecordStatus.FAILED
    assert result.errors[-1].code == "CONTENT_DELETED_LLM"
    [candidate] = engine.invalid_candidates
    assert candidate.code == "CONTENT_DELETED_LLM"
    assert candidate.citation == _DELETED_QUOTE
