"""RegionCaptureService 离线测试：FakePage 模式，无真实浏览器/外网。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot.region_capture import (
    RegionCaptureResult,
    RegionCaptureService,
    _clip_from_payload,
    _unique_capture_name,
)


class FakeRegionPage:
    def __init__(self, *, blank: bool = False, fail: bool = False) -> None:
        self.blank = blank
        self.fail = fail
        self.evaluated: list[str] = []
        self.shot_options: dict[str, object] | None = None

    async def evaluate(self, script: str, *_args: object) -> None:
        self.evaluated.append(script)
        return None

    async def screenshot(self, **options: object) -> None:
        self.shot_options = options
        if self.fail:
            raise RuntimeError("boom")
        clip = options["clip"]
        assert isinstance(clip, dict)
        size = (int(clip["width"]), int(clip["height"]))
        image = Image.new("RGB", size, "#ffffff")
        if not self.blank:
            for x in range(0, size[0], 16):
                image.paste("#2f6f9f", (x, 0, min(size[0], x + 8), size[1]))
        image.save(
            str(options["path"]),
            format="JPEG" if options.get("type") == "jpeg" else "PNG",
        )


def _service() -> RegionCaptureService:
    return RegionCaptureService(TaskConfig())


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


def test_unique_capture_name_pattern_and_collision(tmp_path: Path) -> None:
    name = _unique_capture_name(tmp_path, 7, "content", "jpeg")
    assert name.startswith("007_content_")
    assert name.endswith(".jpg")
    (tmp_path / name).write_bytes(b"x")
    second = _unique_capture_name(tmp_path, 7, "content", "jpeg")
    assert second != name
    assert second.endswith(".jpg")
    author = _unique_capture_name(tmp_path, 7, "author", "png")
    assert author.startswith("007_author_")
    assert author.endswith(".png")


@pytest.mark.asyncio
async def test_handle_action_cancel_finishes(tmp_path: Path) -> None:
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        FakeRegionPage(),
        json.dumps({"action": "cancel"}),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        finish=results.append,
    )
    assert [result.status for result in results] == ["cancelled"]


@pytest.mark.asyncio
async def test_handle_action_confirm_saves_clip(tmp_path: Path) -> None:
    page = FakeRegionPage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        page,
        _confirm_payload(x=10, y=20, w=200, h=120),
        evidence_id=3,
        target="author",
        assets_dir=tmp_path,
        finish=results.append,
    )
    assert [result.status for result in results] == ["saved"]
    name = results[0].name
    assert name.startswith("003_author_")
    assert (tmp_path / name).is_file()
    clip = page.shot_options["clip"] if page.shot_options else None
    assert clip == {"x": 10, "y": 20, "width": 200, "height": 120}
    # 截图前先隐藏浮层
    assert any("__poirRegionCaptureHide" in script for script in page.evaluated)


@pytest.mark.asyncio
async def test_handle_action_rejects_tiny_region(tmp_path: Path) -> None:
    page = FakeRegionPage()
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        page,
        _confirm_payload(w=4, h=4),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        finish=results.append,
    )
    assert results == []
    assert page.shot_options is None
    assert any("__poirRegionCaptureReset" in script for script in page.evaluated)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_handle_action_rejects_blank_capture(tmp_path: Path) -> None:
    page = FakeRegionPage(blank=True)
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        page,
        _confirm_payload(),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        finish=results.append,
    )
    assert results == []
    assert list(tmp_path.iterdir()) == []  # 空白图已删除
    assert any("__poirRegionCaptureReset" in script for script in page.evaluated)


@pytest.mark.asyncio
async def test_handle_action_screenshot_failure_is_error(tmp_path: Path) -> None:
    page = FakeRegionPage(fail=True)
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        page,
        _confirm_payload(),
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
        finish=results.append,
    )
    assert [result.status for result in results] == ["error"]
    assert "截图失败" in results[0].message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_handle_action_ignores_malformed_payload(tmp_path: Path) -> None:
    results: list[RegionCaptureResult] = []
    await _service()._handle_action(
        FakeRegionPage(),
        "not-json",
        evidence_id=1,
        target="content",
        assets_dir=tmp_path,
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
