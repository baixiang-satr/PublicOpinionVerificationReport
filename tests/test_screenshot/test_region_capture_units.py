"""Region capture 纯函数单元测试（clip 校验/命名/裁图）。

Split from ``test_region_capture.py`` to keep every file under the 500-line
release-check limit.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from src.config.settings import TaskConfig
from src.screenshot.region_capture import (
    _capture_name,
    _clip_from_payload,
    _save_region,
)


def _striped_image(size: tuple[int, int] = (800, 600), *, blank: bool = False) -> Image.Image:
    image = Image.new("RGB", size, "#ffffff")
    if not blank:
        for x in range(0, size[0], 16):
            image.paste("#2f6f9f", (x, 0, min(size[0], x + 8), size[1]))
    return image


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
