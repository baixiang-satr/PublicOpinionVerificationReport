"""整页长图补录侧离线测试：手动长图处理、window_capture 分支与动作路由。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot import long_capture, region_capture, window_capture
from src.screenshot.long_capture import handle_manual_long_capture
from src.screenshot.region_capture import (
    RegionCaptureResult,
    RegionCaptureService,
    _CaptureState,
)

# ── 补录「截取长图」处理 ──


def _striped_image(width: int = 800, height: int = 600) -> Image.Image:
    image = Image.new("RGB", (width, height), "#f4f5f6")
    for x in range(0, width, 16):
        image.paste("#2f6f9f", (x, 0, min(width, x + 8), height))
    return image


class _BarePage:
    """无 wait_for_function：readiness gate 立即返回，对齐兜底 False。"""

    async def evaluate(self, _script: str, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_manual_long_capture_saves_named_jpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _capture(
        _page: object,
        output_path: Path,
        _config: TaskConfig,
        *,
        long_page: bool = False,
    ) -> None:
        assert long_page is True
        image = _striped_image(1_456, 5_088)
        try:
            image.save(str(output_path), format="JPEG", quality=82)
        finally:
            image.close()

    monkeypatch.setattr(long_capture, "capture_browser_window", _capture)
    result = await handle_manual_long_capture(
        _BarePage(),
        TaskConfig(),
        evidence_id=7,
        target="content",
        assets_dir=tmp_path,
    )
    assert result == RegionCaptureResult(status="saved", name="007_content.jpg")
    assert (tmp_path / "007_content.jpg").is_file()


@pytest.mark.asyncio
async def test_manual_long_capture_rejects_blank(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _capture(
        _page: object,
        output_path: Path,
        _config: TaskConfig,
        *,
        long_page: bool = False,
    ) -> None:
        image = Image.new("RGB", (1_456, 5_088), "#f4f5f6")
        try:
            image.save(str(output_path), format="JPEG", quality=82)
        finally:
            image.close()

    monkeypatch.setattr(long_capture, "capture_browser_window", _capture)
    result = await handle_manual_long_capture(
        _BarePage(),
        TaskConfig(),
        evidence_id=7,
        target="author",
        assets_dir=tmp_path,
    )
    assert result.status == "error"
    assert "空白" in result.message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_manual_long_capture_reports_capture_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("window gone")

    monkeypatch.setattr(long_capture, "capture_browser_window", _boom)
    result = await handle_manual_long_capture(
        _BarePage(),
        TaskConfig(),
        evidence_id=7,
        target="content",
        assets_dir=tmp_path,
    )
    assert result.status == "error"
    assert "长图截图失败" in result.message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_manual_long_capture_without_page_is_error(tmp_path: Path) -> None:
    result = await handle_manual_long_capture(
        None,
        TaskConfig(),
        evidence_id=7,
        target="content",
        assets_dir=tmp_path,
    )
    assert result.status == "error"
    assert "没有可截取的页面" in result.message


# ── window_capture 长图分支 ──


class _FakeWindowPage:
    def __init__(self) -> None:
        self.titles: list[str] = []

    async def title(self) -> str:
        return "原页面标题"

    async def evaluate(self, script: str, *args: object) -> object:
        if "document.title = t" in script and args:
            self.titles.append(str(args[0]))
        return None

    async def bring_to_front(self) -> None:
        return None


@pytest.fixture
def capture_os_double(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {"printed": []}
    monkeypatch.setattr(window_capture, "_find_window_by_title", lambda _t: 4321)
    monkeypatch.setattr(window_capture, "FIND_WINDOW_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(window_capture, "TAB_STRIP_REPAINT_SECONDS", 0)

    def _print(hwnd: int) -> Image.Image:
        state["printed"].append(hwnd)  # type: ignore[union-attr]
        return _striped_image(1_456, 988)

    monkeypatch.setattr(window_capture, "_print_window", _print)
    return state


@pytest.mark.asyncio
async def test_window_capture_long_page_delegates_to_expand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_os_double: dict[str, object],
) -> None:
    calls: list[tuple[int, Path]] = []

    async def _expand(
        _page: object,
        hwnd: int,
        output_path: Path,
        _config: TaskConfig,
    ) -> None:
        calls.append((hwnd, output_path))
        output_path.write_bytes(b"jpeg-bytes")

    monkeypatch.setattr(long_capture, "expand_and_capture", _expand)
    page = _FakeWindowPage()
    output = tmp_path / "001.jpg"

    await window_capture.capture_browser_window(
        page,
        output,
        TaskConfig(),
        long_page=True,
    )

    assert calls == [(4321, output)]
    assert capture_os_double["printed"] == []  # 未走普通视口抓窗
    assert page.titles[-1] == "原页面标题"  # 标题 token 已恢复
    assert output.read_bytes() == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_window_capture_long_page_failure_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_os_double: dict[str, object],
) -> None:
    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("expand failed")

    monkeypatch.setattr(long_capture, "expand_and_capture", _boom)
    output = tmp_path / "001.jpg"

    await window_capture.capture_browser_window(
        _FakeWindowPage(),
        output,
        TaskConfig(),
        long_page=True,
    )

    assert capture_os_double["printed"] == [4321]  # 回退普通视口抓窗
    with Image.open(output) as decoded:
        assert decoded.format == "JPEG"


@pytest.mark.asyncio
async def test_window_capture_default_stays_viewport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_os_double: dict[str, object],
) -> None:
    async def _expand(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("long-page capture must not run by default")

    monkeypatch.setattr(long_capture, "expand_and_capture", _expand)
    output = tmp_path / "001.jpg"

    await window_capture.capture_browser_window(_FakeWindowPage(), output, TaskConfig())

    assert capture_os_double["printed"] == [4321]
    assert output.is_file()


# ── 补录动作路由与按钮显隐 ──


class _ToolbarProbe:
    def __init__(self) -> None:
        self.messages: list[str | None] = []

    def show(self, message: str | None = None) -> None:
        self.messages.append(message)


def _capture_state() -> _CaptureState:
    return _CaptureState(context=None, browse_page=_BarePage())


@pytest.mark.asyncio
async def test_region_action_long_finishes_on_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    async def _fake_long(
        page: object,
        config: TaskConfig,
        *,
        evidence_id: int,
        target: str,
        assets_dir: Path,
        focus_texts: tuple[str, ...] = (),
    ) -> RegionCaptureResult:
        received.update(
            evidence_id=evidence_id,
            target=target,
            assets_dir=assets_dir,
            focus_texts=focus_texts,
        )
        return RegionCaptureResult(status="saved", name="003_author.jpg")

    monkeypatch.setattr(region_capture, "handle_manual_long_capture", _fake_long)
    state = _capture_state()
    state.focus_texts = ("作者昵称",)
    results: list[RegionCaptureResult] = []
    await RegionCaptureService(TaskConfig())._handle_action(
        state.browse_page,
        json.dumps({"action": "long"}),
        evidence_id=3,
        target="author",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert [result.status for result in results] == ["saved"]
    assert received == {
        "evidence_id": 3,
        "target": "author",
        "assets_dir": tmp_path,
        "focus_texts": ("作者昵称",),
    }


@pytest.mark.asyncio
async def test_region_action_long_error_reshows_toolbar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_long(*_args: object, **_kwargs: object) -> RegionCaptureResult:
        return RegionCaptureResult(status="error", message="长图截图失败：boom")

    monkeypatch.setattr(region_capture, "handle_manual_long_capture", _fake_long)
    state = _capture_state()
    toolbar = _ToolbarProbe()
    state.toolbar = toolbar
    results: list[RegionCaptureResult] = []
    await RegionCaptureService(TaskConfig())._handle_action(
        state.browse_page,
        json.dumps({"action": "long"}),
        evidence_id=3,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert results == []  # 失败不终结会话，操作员可重试
    assert toolbar.messages == ["长图截图失败：boom"]


# ── 服务层平台门控（platform_key → show_long 按钮显隐）──


class _PlumbingNavPage:
    def __init__(self) -> None:
        self.closed = False

    async def goto(self, _url: str, **_kwargs: object) -> None:
        return None

    async def evaluate(self, *_args: object) -> None:
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def on(self, *_args: object) -> None:
        return None

    def remove_listener(self, *_args: object) -> None:
        return None

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class _PlumbingContext:
    def __init__(self, page: _PlumbingNavPage) -> None:
        self.pages: list[_PlumbingNavPage] = [page]

    def on(self, *_args: object) -> None:
        return None

    def remove_listener(self, *_args: object) -> None:
        return None

    async def new_page(self) -> _PlumbingNavPage:
        page = _PlumbingNavPage()
        self.pages.append(page)
        return page


class _PlumbingSession:
    def __init__(self, context: _PlumbingContext, page: _PlumbingNavPage) -> None:
        self._context = context
        self._page = page

    async def context_for(self, *_args: object, **_kwargs: object) -> _PlumbingContext:
        return self._context

    async def browse_page_for(self, *_args: object) -> _PlumbingNavPage:
        return self._page

    def set_binding_handler(self, _handler: object) -> None:
        return None

    async def save_states(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _IdleToolbar:
    def start(self) -> None:
        return None

    def show(self, _message: str | None = None) -> None:
        return None

    def hide(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform_key", "expected"),
    [("weibo", True), ("wechat_official", True), ("douyin", False), (None, False)],
)
async def test_capture_toolbar_show_long_follows_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_key: str | None,
    expected: bool,
) -> None:
    recorded: dict[str, object] = {}

    def _open(
        _factory: object,
        loop: asyncio.AbstractEventLoop,
        on_action: object,
        **kwargs: object,
    ) -> _IdleToolbar:
        recorded.update(kwargs)
        loop.call_soon(lambda: on_action({"action": "cancel"}))  # type: ignore[operator]
        return _IdleToolbar()

    monkeypatch.setattr(region_capture, "open_capture_toolbar", _open)
    page = _PlumbingNavPage()
    session = _PlumbingSession(_PlumbingContext(page), page)
    service = RegionCaptureService(
        TaskConfig(),
        session=session,  # type: ignore[arg-type]
        toolbar_factory=lambda *_a, **_k: _IdleToolbar(),
    )

    result = await service.capture(
        "https://weibo.com/1234567890/AbCdEf",
        evidence_id=1,
        target="content",
        platform_key=platform_key,
        storage_state=None,
        assets_dir=tmp_path,
    )

    assert result.status == "cancelled"
    assert recorded["show_long"] is expected
