"""OcrPipeline 截图字段恢复的超高长图顶部裁剪离线测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.config.settings import TaskConfig
from src.crawler.ocr_pipeline import (
    OcrPipeline,
    _TALL_SCREENSHOT_OCR_CROP,
    _crop_tall_screenshot,
)
from src.domain.models import OcrStatus, PageData


class FakeOcrClient:
    def __init__(self) -> None:
        self.paths_seen: list[list[Path]] = []

    def recognize(
        self,
        paths: list[Path],
        *,
        confidence_threshold: float,
        cancelled: object,
    ) -> SimpleNamespace:
        self.paths_seen.append([Path(item) for item in paths])
        return SimpleNamespace(
            status=OcrStatus.SUCCESS,
            text="某公众号文章标题\n2026-09-30 10:00",
            error="",
            text_image_count=1,
        )

    def close(self) -> None:
        return None


def _striped_tall(path: Path, height: int) -> Path:
    image = Image.new("RGB", (800, height), "#ffffff")
    for y in range(0, height, 64):
        image.paste("#2f6f9f", (0, y, 800, min(height, y + 32)))
    try:
        image.save(str(path), format="JPEG", quality=80)
    finally:
        image.close()
    return path


def _page() -> PageData:
    # 标题/正文/时间全缺 → 触发截图字段恢复。
    return PageData(final_url="https://mp.weixin.qq.com/s/abc123")


@pytest.mark.asyncio
async def test_tall_screenshot_is_cropped_before_ocr(tmp_path: Path) -> None:
    screenshot = _striped_tall(tmp_path / "001.jpg", _TALL_SCREENSHOT_OCR_CROP + 2_000)
    client = FakeOcrClient()
    pipeline = OcrPipeline(TaskConfig(), client=client)

    errors = await pipeline.recover_screenshot_fields(
        _page(),
        screenshot,
        asyncio.Event(),
    )

    assert errors == []
    assert len(client.paths_seen) == 1
    ocr_input = client.paths_seen[0][0]
    assert ocr_input.name == "001.ocr-top.jpg"
    assert not ocr_input.exists()  # 临时裁剪图用后即删
    assert screenshot.is_file()  # 原长图不受影响


@pytest.mark.asyncio
async def test_short_screenshot_goes_to_ocr_as_is(tmp_path: Path) -> None:
    screenshot = _striped_tall(tmp_path / "002.jpg", 900)
    client = FakeOcrClient()
    pipeline = OcrPipeline(TaskConfig(), client=client)

    errors = await pipeline.recover_screenshot_fields(
        _page(),
        screenshot,
        asyncio.Event(),
    )

    assert errors == []
    assert client.paths_seen == [[screenshot]]
    assert not (tmp_path / "002.ocr-top.jpg").exists()


def test_crop_tall_screenshot_top_slice(tmp_path: Path) -> None:
    screenshot = _striped_tall(tmp_path / "003.jpg", _TALL_SCREENSHOT_OCR_CROP + 500)

    cropped = _crop_tall_screenshot(screenshot)
    try:
        assert cropped.name == "003.ocr-top.jpg"
        with Image.open(cropped) as decoded:
            assert decoded.size == (800, _TALL_SCREENSHOT_OCR_CROP)
    finally:
        cropped.unlink(missing_ok=True)


def test_crop_tall_screenshot_passes_through_short_and_broken(tmp_path: Path) -> None:
    short = _striped_tall(tmp_path / "004.jpg", 1_000)
    assert _crop_tall_screenshot(short) == short

    broken = tmp_path / "005.jpg"
    broken.write_bytes(b"not-an-image")
    assert _crop_tall_screenshot(broken) == broken  # 探测失败按原图识别
