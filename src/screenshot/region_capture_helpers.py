"""Pure helpers for the interactive region-capture flow.

Split from :mod:`src.screenshot.region_capture` to keep each module under
the repository's per-file line limit.  Everything here is UI-independent:
overlay evaluate helpers, payload validation, region naming/cropping and
the frozen-screen selection-tab document builder.  ``region_capture``
re-exports the public surface used by the test-suite.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.settings import TaskConfig
from src.crawler.navigation import navigate_page, stabilize_rendered_page
from src.screenshot.region_capture_scripts import SELECTION_HTML
from src.utils.file_utils import require_safe_file_name

if TYPE_CHECKING:
    from PIL import Image

_MIN_REGION_PX = 8

#: 框选预览图最长边：多屏 4K 冻结图原尺寸内嵌会让 WebView2 内存暴涨。
_PREVIEW_MAX_EDGE = 1920


@dataclass
class _CaptureState:
    """Mutable per-window state shared between binding callbacks."""

    context: Any = None
    browse_page: Any = None
    select_page: Any = None
    toolbar: Any = None
    focus_texts: tuple[str, ...] = ()
    image: Any = None  # PIL.Image of the frozen full screen
    image_scale: float = 1.0  # 原图 / 预览图 比例（≥1，裁剪时回映坐标）
    select_pending: bool = False  # selection tab is being created


def uses_desktop_profile_context(
    platform_key: str | None,
    target: str,
) -> bool:
    """Keep Kuaishou profile capture off its mobile share-page context."""

    return platform_key == "kuaishou" and target == "author"


async def navigate_and_stabilize(
    page: Any,
    url: str,
    config: TaskConfig,
    cancel_event: asyncio.Event | None,
) -> None:
    """Navigate the browse window; a failed trip leaves it open for repair."""

    try:
        await navigate_page(
            page,
            url,
            config.page_timeout_seconds * 1000,
            cancel_event,
        )
        await stabilize_rendered_page(page, config.page_stabilize_milliseconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def wait_for_capture_result(
    done: asyncio.Future[Any],
    cancel_event: asyncio.Event | None,
    cancelled_factory: Callable[[], Any],
) -> Any:
    if cancel_event is None:
        return await done
    cancel_waiter = asyncio.ensure_future(cancel_event.wait())
    try:
        await asyncio.wait(
            {asyncio.ensure_future(done), cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        return done.result() if done.done() else cancelled_factory()
    finally:
        cancel_waiter.cancel()
        await asyncio.gather(cancel_waiter, return_exceptions=True)


def page_is_closed(page: Any) -> bool:
    """True when a Playwright page is unusable or already closed."""

    checker = getattr(page, "is_closed", None)
    try:
        return bool(checker()) if callable(checker) else False
    except Exception:  # noqa: BLE001 - a broken page is unusable
        return True


def _live_pages(context: Any, *excluded: Any) -> list[Any]:
    """Open pages in a context, ignoring the given instances."""

    return [
        candidate
        for candidate in list(getattr(context, "pages", ()) or ())
        if all(candidate is not item for item in excluded)
        and not page_is_closed(candidate)
    ]


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

    left = min(max(0, clip["x"]), image.width)
    top = min(max(0, clip["y"]), image.height)
    right = min(image.width, max(0, clip["x"]) + clip["width"])
    bottom = min(image.height, max(0, clip["y"]) + clip["height"])
    if right - left < _MIN_REGION_PX or bottom - top < _MIN_REGION_PX:
        raise ValueError("Selected region falls outside the captured screen.")
    box = (left, top, right, bottom)
    region = image.crop(box)
    try:
        if config.screenshot_format == "jpeg":
            region.convert("RGB").save(
                str(output), format="JPEG", quality=config.screenshot_jpeg_quality
            )
        else:
            region.save(str(output), format="PNG")
    finally:
        region.close()


def _scale_clip(clip: dict[str, int], factor: float) -> dict[str, int]:
    """把预览图坐标系的选区回映到原图坐标系（预览降采样时 factor>1）。"""

    if factor == 1.0:
        return clip
    return {
        "x": round(clip["x"] * factor),
        "y": round(clip["y"] * factor),
        "width": round(clip["width"] * factor),
        "height": round(clip["height"] * factor),
    }


def _selection_html(image: "Image.Image") -> tuple[str, float]:
    """Selection-tab document + 原图/预览图比例；预览最长边受限以控制内存。"""

    from PIL import Image

    width, height = image.size
    ratio = max(width, height) / _PREVIEW_MAX_EDGE
    preview = image
    if ratio > 1.0:
        preview = image.resize(
            (round(width / ratio), round(height / ratio)),
            Image.Resampling.LANCZOS,
        )
    pw, ph = preview.size
    buffer = io.BytesIO()
    try:
        preview.convert("RGB").save(buffer, format="JPEG", quality=82)
    finally:
        if preview is not image:
            preview.close()
    data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    scale = width / pw
    return (
        SELECTION_HTML.replace("__IMG_SRC__", data_url)
        .replace("__IMG_W__", str(pw))
        .replace("__IMG_H__", str(ph)),
        scale,
    )
