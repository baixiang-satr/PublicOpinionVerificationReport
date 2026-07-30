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
    _CaptureState,
    _capture_name,
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
    context = FakeContext()
    state = _state(context)
    results: list[RegionCaptureResult] = []
    await _service(_striped_image)._handle_action(
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
    # 截屏前先隐藏浏览页工具条
    assert any("__poirRegionCaptureHide" in script for script in state.browse_page.scripts())


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
