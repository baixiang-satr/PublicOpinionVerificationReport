"""Pure helpers for the interactive region-capture flow.

Split from :mod:`src.screenshot.region_capture` to keep every module under
the repository's per-file line limit.  Everything here is synchronous and
browser-free: payload validation, file naming, screen grabbing, region
cropping and selection-tab HTML rendering.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.settings import TaskConfig
from src.screenshot.region_capture_scripts import SELECTION_HTML
from src.utils.file_utils import require_safe_file_name

if TYPE_CHECKING:
    from PIL import Image

_MIN_REGION_PX = 8


def _clip_from_payload(data: dict[str, Any]) -> dict[str, int] | None:
    """Validate the JS-reported frozen-image rect into a crop box dict."""

    try:
        x = float(data["x"])
        y = float(data["y"])
        width = float(data["width"])
        height = float(data["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width < _MIN_REGION_PX or height < _MIN_REGION_PX:
        return None
    return {
        "x": max(0, int(x)),
        "y": max(0, int(y)),
        "width": min(32_767, int(width)),
        "height": min(32_767, int(height)),
    }


def _capture_name(evidence_id: int, target: str, screenshot_format: str) -> str:
    """标准化命名：``001_content.jpg`` / ``001_author.jpg``。

    同一记录同一槽位重新截取时直接覆盖同名文件，避免 manual_assets 里
    堆积失效的旧截图；命名刻意避开审计正则 ``\\d{3}主页``。
    """

    extension = "jpg" if screenshot_format == "jpeg" else "png"
    return require_safe_file_name(f"{evidence_id:03d}_{target}.{extension}")


def _grab_full_screen() -> "Image.Image":
    """OS-level screenshot of the whole virtual screen (all monitors)."""

    from PIL import ImageGrab

    return ImageGrab.grab(all_screens=True)


def _save_region(
    config: TaskConfig,
    image: "Image.Image",
    clip: dict[str, int],
    output: Path,
) -> None:
    """Crop the confirmed region out of the frozen screen image."""

    box = (
        clip["x"],
        clip["y"],
        clip["x"] + clip["width"],
        clip["y"] + clip["height"],
    )
    region = image.crop(box)
    if config.screenshot_format == "jpeg":
        region.convert("RGB").save(
            str(output), format="JPEG", quality=config.screenshot_jpeg_quality
        )
    else:
        region.save(str(output), format="PNG")


def _selection_html(image: "Image.Image") -> str:
    """Selection-tab document: frozen screen + rubber band, image-space coords."""

    width, height = image.size
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=82)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        SELECTION_HTML.replace("__IMG_SRC__", data_url)
        .replace("__IMG_W__", str(width))
        .replace("__IMG_H__", str(height))
    )
