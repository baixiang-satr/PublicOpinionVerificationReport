"""Pure helpers for the interactive region-capture flow.

Split from :mod:`src.screenshot.region_capture` to keep each module under
the repository's per-file line limit.  Everything here is UI-independent:
overlay evaluate helpers, payload validation, region naming/cropping and
the frozen-screen selection-tab document builder.  ``region_capture``
re-exports the public surface used by the test-suite.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.settings import TaskConfig
from src.screenshot.region_capture_scripts import SELECTION_HTML
from src.utils.file_utils import require_safe_file_name

if TYPE_CHECKING:
    from PIL import Image

_MIN_REGION_PX = 8


@dataclass
class _CaptureState:
    """Mutable per-window state shared between binding callbacks."""

    context: Any = None
    browse_page: Any = None
    select_page: Any = None
    image: Any = None  # PIL.Image of the frozen full screen
    select_pending: bool = False  # selection tab is being created


async def _hide_overlay(page: Any) -> None:
    if page is None:
        return
    try:
        await page.evaluate(
            "() => window.__poirRegionCaptureHide && window.__poirRegionCaptureHide()"
        )
    except Exception:  # noqa: BLE001 — 页面可能已关闭
        pass


async def _reset_overlay(page: Any, message: str | None) -> None:
    if page is None:
        return
    try:
        await page.evaluate(
            "(msg) => window.__poirRegionCaptureReset && window.__poirRegionCaptureReset(msg)",
            message or "",
        )
    except Exception:  # noqa: BLE001 — 页面可能已关闭
        pass


async def _reset_selection(state: _CaptureState, message: str) -> None:
    page = state.select_page
    if page is None:
        return
    try:
        await page.evaluate(
            "(msg) => window.__poirSelectReset && window.__poirSelectReset(msg)",
            message,
        )
    except Exception:  # noqa: BLE001 — 页面可能已关闭
        pass


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
