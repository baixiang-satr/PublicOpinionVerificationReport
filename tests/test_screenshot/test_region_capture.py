"""RegionCaptureService 离线测试：FakePage/FakeContext 模式，无真实浏览器/外网。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot import region_capture
from src.screenshot.region_capture import (
    RegionCaptureResult,
    RegionCaptureService,
    _capture_name,
    _CaptureState,
    _clip_from_payload,
    _save_region,
    _selection_html,
)


class FakePage:
    def __init__(self) -> None:
        self.evaluated: list[tuple[str, tuple[object, ...]]] = []
        self.content: str | None = None
        self.closed = False

    async def evaluate(self, script: str, *args: object) -> None:
        self.evaluated.append((script, args))
        return None

    async def set_content(self, html: str) -> None:
        self.content = html

    def on(self, _event: str, _handler: object) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    def scripts(self) -> list[str]:
        return [script for script, _args in self.evaluated]


class HorizontallyOffsetPage(FakePage):
    async def evaluate(self, script: str, *args: object) -> object:
        self.evaluated.append((script, args))
        if "documentWidth" in script and "needsHorizontalAlignment" in script:
            return {
                "viewportWidth": 1_440,
                "documentWidth": 4_000,
                "height": 1_200,
                "focusX": 1_100,
                "scrollX": 0,
                "needsHorizontalAlignment": True,
            }
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


def _striped_image(size: tuple[int, int] = (800, 600), *, blank: bool = False) -> Image.Image:
    image = Image.new("RGB", size, "#ffffff")
    if not blank:
        for x in range(0, size[0], 16):
            image.paste("#2f6f9f", (x, 0, min(size[0], x + 8), size[1]))
    return image


def _service(grabber=None) -> RegionCaptureService:
    return RegionCaptureService(TaskConfig(), screen_grabber=grabber)


def _state(context: FakeContext | None = None, image: Image.Image | None = None) -> _CaptureState:
    ctx = context or FakeContext()
    state = _CaptureState(context=ctx, browse_page=FakePage())
    state.image = image
    return state


def _confirm_payload(x: float = 10, y: float = 20, w: float = 200, h: float = 120) -> str:
    return json.dumps({"action": "confirm", "x": x, "y": y, "width": w, "height": h})


def test_clip_from_payload_validates_and_clamps() -> None:
    assert _clip_from_payload({"x": 10.6, "y": 20.2, "width": 200.9, "height": 120.1}) == {
        "x": 10,
        "y": 20,
        "width": 200,
        "height": 120,
    }
    assert _clip_from_payload({"x": -5, "y": -8, "width": 50, "height": 50}) == {
        "x": 0,
        "y": 0,
        "width": 50,
        "height": 50,
    }
    # 过小 / 缺字段 / 非数值都被拒绝
    assert _clip_from_payload({"x": 0, "y": 0, "width": 4, "height": 50}) is None
    assert _clip_from_payload({"x": 0, "y": 0, "width": 50}) is None
    assert _clip_from_payload({"x": "a", "y": 0, "width": 50, "height": 50}) is None
    assert _clip_from_payload({}) is None


def test_capture_name_standardized_per_slot() -> None:
    assert _capture_name(7, "content", "jpeg") == "007_content.jpg"
    assert _capture_name(7, "author", "jpeg") == "007_author.jpg"
    assert _capture_name(3, "content", "png") == "003_content.png"
    # 命名不得触发“主页”审计正则（人工截图没有决策 sidecar）
    import re

    assert not re.match(r"^\d{3}主页\.", _capture_name(7, "author", "jpeg"))


def test_save_region_crops_frozen_image(tmp_path: Path) -> None:
    output = tmp_path / "001_content.jpg"
    _save_region(
        TaskConfig(),
        _striped_image((800, 600)),
        {"x": 10, "y": 20, "width": 200, "height": 120},
        output,
    )
    with Image.open(output) as saved:
        assert saved.size == (200, 120)
        assert saved.format == "JPEG"


# ── 常驻会话模式 ──


def test_overlay_js_self_heals_after_spa_navigation() -> None:
    """工具条必须有自愈钩子：SPA 跳转/框架重渲染清 DOM 后自动重建。"""

    from src.screenshot.region_capture_scripts import OVERLAY_JS

    assert "setInterval" in OVERLAY_JS
    assert "__poir-shot-host" in OVERLAY_JS
    assert "location.href" in OVERLAY_JS
    assert "popstate" in OVERLAY_JS


class FakeNavPage(FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.navigated: list[str] = []
        self.listeners: dict[str, list] = {}

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.navigated.append(url)
        return None

    def on(self, event: str, handler: object) -> None:
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: object) -> None:
        if handler in self.listeners.get(event, []):
            self.listeners[event].remove(handler)


class FakeSessionContext:
    def __init__(self) -> None:
        self.pages: list[FakeNavPage] = []
        self.listeners: dict[str, list] = {}

    def on(self, event: str, handler: object) -> None:
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: object) -> None:
        if handler in self.listeners.get(event, []):
            self.listeners[event].remove(handler)

    async def new_page(self) -> FakeNavPage:
        page = FakeNavPage()
        self.pages.append(page)
        return page


class FakeSession:
    def __init__(self, context: FakeSessionContext, page: FakeNavPage) -> None:
        self._context = context
        self._page = page
        self.handler = None
        self.saved = 0
        self.closed = 0

    async def context_for(self, _key: str, _storage_state: object) -> FakeSessionContext:
        return self._context

    async def browse_page_for(self, _key: str, _context: object) -> FakeNavPage:
        return self._page

    def set_binding_handler(self, handler: object) -> None:
        self.handler = handler

    async def save_states(self) -> None:
        self.saved += 1

    async def close(self) -> None:
        self.closed += 1
        await self.save_states()


@pytest.mark.asyncio
async def test_session_capture_registers_and_clears_binding_handler(
    tmp_path: Path,
) -> None:
    context = FakeSessionContext()
    page = FakeNavPage()
    session = FakeSession(context, page)
    service = RegionCaptureService(TaskConfig(), session=session)

    task = asyncio.create_task(
        service.capture(
            "https://www.douyin.com/video/123",
            evidence_id=7,
            target="content",
            platform_key="douyin",
            storage_state=None,
            assets_dir=tmp_path,
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert session.handler is not None

    await session.handler({"page": page}, json.dumps({"action": "cancel"}))
    result = await task

    assert result.status == "cancelled"
    assert session.handler is None  # 结束后清空，不影响下一次截图
    assert session.saved == 1  # 关闭前登录态回写
    assert session.closed == 1  # 完成后自动退出浏览器
    assert page.navigated == ["https://www.douyin.com/video/123"]
    assert context.listeners.get("page") == []  # 监听不累积
    assert page.listeners.get("close") == []


def test_selection_html_embeds_frozen_image() -> None:
    html = _selection_html(_striped_image((640, 480)))
    assert html.startswith("<!doctype html>")
    assert "var IW = 640, IH = 480;" in html
    assert "data:image/jpeg;base64," in html
    assert "__IMG_SRC__" not in html and "__IMG_W__" not in html


@pytest.mark.asyncio
async def test_handle_action_cancel_finishes(tmp_path: Path) -> None:
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        FakePage(),
        json.dumps({"action": "cancel"}),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=_state(),
        finish=results.append,
    )
    assert [result.status for result in results] == ["cancelled"]


@pytest.mark.asyncio
async def test_arm_grabs_screen_and_opens_selection_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(region_capture, "_ARM_DELAY_SECONDS", 0)
    events: list[str] = []

    async def ready(*_args: object, **_kwargs: object) -> bool:
        events.append("ready")
        return True

    def grab_after_ready() -> Image.Image:
        events.append("grab")
        return _striped_image()

    monkeypatch.setattr(region_capture, "wait_for_capture_ready", ready)
    context = FakeContext()
    state = _state(context)
    results: list[RegionCaptureResult] = []
    await _service(grab_after_ready)._handle_action(
        state.browse_page,
        json.dumps({"action": "arm"}),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert results == []
    assert state.image is not None
    assert state.select_page is context.pages[0]
    assert state.select_page.content is not None
    assert "data:image/jpeg;base64," in state.select_page.content
    assert events == ["ready", "grab"]
    # 截屏前先隐藏浏览页工具条
    assert any("__poirRegionCaptureHide" in script for script in state.browse_page.scripts())


@pytest.mark.asyncio
async def test_arm_aligns_cross_site_horizontal_overflow_before_screen_grab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(region_capture, "_ARM_DELAY_SECONDS", 0)
    context = FakeContext()
    page = HorizontallyOffsetPage()
    state = _CaptureState(context=context, browse_page=page)

    await _service(_striped_image)._arm(state)

    scroll_calls = [
        args
        for script, args in page.evaluated
        if "window.scrollTo(left, top)" in script
    ]
    assert scroll_calls == [(1_100,)]
    assert state.image is not None
    assert state.select_page is context.pages[0]


@pytest.mark.asyncio
async def test_arm_grabber_failure_recovers_to_browse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(region_capture, "_ARM_DELAY_SECONDS", 0)

    def broken() -> Image.Image:
        raise RuntimeError("no display")

    state = _state()
    await _service(broken)._handle_action(
        state.browse_page,
        json.dumps({"action": "arm"}),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=lambda _r: None,
    )
    assert state.image is None
    assert state.select_page is None
    resets = [
        args for script, args in state.browse_page.evaluated
        if "__poirRegionCaptureReset" in script
    ]
    assert resets and "无法截取屏幕" in str(resets[-1][0])


@pytest.mark.asyncio
async def test_handle_action_confirm_saves_clip(tmp_path: Path) -> None:
    state = _state(image=_striped_image())
    state.select_page = FakePage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        state.select_page,
        _confirm_payload(x=10, y=20, w=200, h=120),
        evidence_id=3,
        target="author",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert [result.status for result in results] == ["saved"]
    assert results[0].name == "003_author.jpg"
    assert (tmp_path / "003_author.jpg").is_file()


@pytest.mark.asyncio
async def test_handle_action_rejects_tiny_region(tmp_path: Path) -> None:
    state = _state(image=_striped_image())
    state.select_page = FakePage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        state.select_page,
        _confirm_payload(w=4, h=4),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert results == []
    assert any("__poirSelectReset" in script for script in state.select_page.scripts())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_handle_action_rejects_blank_capture(tmp_path: Path) -> None:
    state = _state(image=_striped_image(blank=True))
    state.select_page = FakePage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        state.select_page,
        _confirm_payload(),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert results == []
    assert list(tmp_path.iterdir()) == []  # 空白图已删除
    assert any("__poirSelectReset" in script for script in state.select_page.scripts())


@pytest.mark.asyncio
async def test_handle_action_confirm_without_image_resets(tmp_path: Path) -> None:
    state = _state(image=None)
    state.select_page = FakePage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        state.select_page,
        _confirm_payload(),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert results == []
    assert any("__poirSelectReset" in script for script in state.select_page.scripts())


@pytest.mark.asyncio
async def test_abort_closes_selection_and_restores_browse(tmp_path: Path) -> None:
    state = _state(image=_striped_image())
    state.select_page = FakePage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        state.select_page,
        json.dumps({"action": "abort"}),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=state,
        finish=results.append,
    )
    assert results == []
    assert state.select_page is None
    assert state.image is None
    assert any("__poirRegionCaptureReset" in script for script in state.browse_page.scripts())


@pytest.mark.asyncio
async def test_handle_action_ignores_malformed_payload(tmp_path: Path) -> None:
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        FakePage(),
        "not-json",
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        state=_state(),
        finish=results.append,
    )
    assert results == []


@pytest.mark.asyncio
async def test_wait_for_result_honours_cancel_event() -> None:
    service = _service()
    loop = asyncio.get_running_loop()

    done: asyncio.Future[RegionCaptureResult] = loop.create_future()
    done.set_result(RegionCaptureResult(status="saved", name="x.jpg"))
    assert (await service._wait_for_result(done, None)).status == "saved"

    pending: asyncio.Future[RegionCaptureResult] = loop.create_future()
    cancel_event = asyncio.Event()
    cancel_event.set()
    assert (await service._wait_for_result(pending, cancel_event)).status == "cancelled"
