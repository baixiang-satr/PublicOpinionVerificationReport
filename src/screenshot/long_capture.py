"""文字类平台整页长图截图：扩窗 + PrintWindow（真实地址栏保留在图顶部）。

微信公众号/百家号/头条/网易新闻/搜狐新闻/凤凰新闻/微博的内容页证据
截图由视口抓窗升级为整页长图：先把浏览器窗口用 SetWindowPos 撑高到
文档高度（视口随窗体扩大，懒加载内容随之渲染），再用既有
PrintWindow(PW_RENDERFULLCONTENT) 一次抓全——标签栏 + 地址栏 + 整页
内容同框，最终 URL 溯源语义与 U06 视口截图完全一致。抓完恢复原窗口
几何（自动抓取的离屏窗口被同平台多标签共享，必须恢复）。

尺寸预算：长图统一 JPEG 落盘（即使 ``screenshot_format=png``），按
``long_screenshot_max_bytes``（默认 1MB）自适应——质量阶梯从
``long_page_jpeg_quality`` 起步逐档下调，仍超预算则按宽度缩放阶梯
重试；全部超预算时写最小一档（有证据图好过无证据图）。视频类平台、
作者主页截图与其余平台不经过本模块，行为不变。
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.settings import TaskConfig
from src.screenshot.capture_ready import wait_for_capture_ready
from src.screenshot.image_checks import UnreadableImageError, is_visually_blank
from src.screenshot.page_layout import align_page_for_capture
from src.screenshot.region_capture_helpers import (
    RegionCaptureResult,
    _capture_name,
)
from src.screenshot.window_capture import _print_window, capture_browser_window

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

#: 内容页整页长图的平台集合（视频类与其余平台保持视口截图不变）。
#: 集合放在本模块而非 platform_data.py——后者已贴近 500 行上限。
LONG_PAGE_PLATFORMS: frozenset[str] = frozenset(
    {
        "wechat_official",
        "baijiahao",
        "toutiao",
        "netease_news",
        "sohu_news",
        "ifeng_news",
        "weibo",
    }
)

#: 懒加载滚动遍历：步长=视口高×比率，有界步数防无尽流页面失控。
_SCROLL_STEP_RATIO = 0.85
_SCROLL_MAX_STEPS = 24
_SCROLL_PAUSE_MS = 150
#: 扩窗后等待重排/图片加载的有界轮询。
_SETTLE_POLL_SECONDS = 0.15
_SETTLE_TIMEOUT_SECONDS = 5.0
_VIEWPORT_TOLERANCE_PX = 4
#: JPEG 尺寸预算编码阶梯：质量逐档下调，再逐级缩小宽度重试。
_QUALITY_STEP = 8
_QUALITY_FLOOR = 40
_SCALE_LADDER = (1.0, 0.85, 0.7)

_SCROLL_STEP_SCRIPT = """(ratio) => {
    const doc = document.documentElement || {};
    const body = document.body || {};
    const docHeight = Math.max(doc.scrollHeight || 0, body.scrollHeight || 0);
    const maxY = Math.max(0, docHeight - window.innerHeight);
    const step = Math.max(200, Math.floor(window.innerHeight * ratio));
    const next = Math.min((window.scrollY || 0) + step, maxY);
    window.scrollTo(0, next);
    return next >= maxY - 2;
}"""

_PENDING_IMAGES_SCRIPT = (
    "() => Array.from(document.images).filter((img) => !img.complete).length"
)

_DOCUMENT_HEIGHT_SCRIPT = """() => {
    const doc = document.documentElement || {};
    const body = document.body || {};
    return Math.ceil(Math.max(
        doc.scrollHeight || 0,
        body.scrollHeight || 0,
        window.innerHeight || 0
    ));
}"""


class LongCaptureError(RuntimeError):
    """Raised when the expanded-window long capture cannot complete."""


def is_long_page_platform(platform_key: str | None) -> bool:
    """True when the platform's content page uses full-page long capture."""

    return bool(platform_key) and platform_key in LONG_PAGE_PLATFORMS


async def expand_and_capture(
    page: Any,
    hwnd: int,
    output_path: Path,
    config: TaskConfig,
) -> None:
    """撑高窗口抓整页长图并按预算编码落盘；窗口几何 finally 恢复。

    调用方（``window_capture._capture_window_locked``）已完成标题 token
    定位与标签激活；本函数负责懒加载触发、文档测量、扩窗、重排等待与
    抓图落盘。任一步失败抛 ``LongCaptureError``，由调用方回退普通视口
    抓窗。
    """

    original_rect = await asyncio.to_thread(_window_rect, hwnd)
    if original_rect is None:
        raise LongCaptureError("Unable to read the browser window geometry.")
    image: Image.Image | None = None
    try:
        await _prime_lazy_content(page)
        target_height = await _measure_document_height(page, config)
        if target_height is None:
            raise LongCaptureError("Unable to measure the document height.")
        viewport_height = await _viewport_metrics(page)
        if viewport_height is None:
            raise LongCaptureError("Unable to read the viewport height.")
        chrome_height = (original_rect[3] - original_rect[1]) - viewport_height
        new_window_height = max(
            original_rect[3] - original_rect[1],
            chrome_height + target_height,
        )
        await asyncio.to_thread(
            _set_window_geometry, hwnd, original_rect, new_window_height
        )
        await _wait_for_viewport(page, target_height)
        await _wait_for_images(page)
        image = await asyncio.to_thread(_print_window, hwnd)
    finally:
        await asyncio.to_thread(_restore_window, hwnd, original_rect)
    try:
        if image is None:
            raise LongCaptureError("Long-page window capture produced no image.")
        await asyncio.to_thread(_save_with_budget, image, output_path, config)
    finally:
        if image is not None:
            image.close()


async def handle_manual_long_capture(
    page: Any,
    config: TaskConfig,
    *,
    evidence_id: int,
    target: str,
    assets_dir: Path,
    focus_texts: tuple[str, ...] = (),
) -> RegionCaptureResult:
    """补录工具条「截取长图」：整页扩窗抓图，命名/空白门槛与框选一致。

    成功返回 ``saved``（调用方收尾关闭会话）；失败返回 ``error`` 并附
    用户可读消息，由调用方重新亮起工具条，操作员可重试或改用框选。
    """

    if page is None:
        return RegionCaptureResult(status="error", message="没有可截取的页面。")
    await wait_for_capture_ready(page, require_content=False)
    await align_page_for_capture(page, focus_texts=focus_texts)
    name = _capture_name(evidence_id, target, "jpeg")
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    output = assets_dir / name
    try:
        await capture_browser_window(page, output, config, long_page=True)
    except Exception as error:  # noqa: BLE001 — 统一回吐给工具条
        output.unlink(missing_ok=True)
        return RegionCaptureResult(
            status="error",
            message=f"长图截图失败：{type(error).__name__}: {error}",
        )
    try:
        blank = is_visually_blank(output)
    except UnreadableImageError:
        blank = True
    if blank:
        output.unlink(missing_ok=True)
        return RegionCaptureResult(
            status="error",
            message="截到的长图是空白，请检查页面后重试。",
        )
    return RegionCaptureResult(status="saved", name=name)


async def _prime_lazy_content(page: Any) -> None:
    """有界滚动遍历触发懒加载（scroll 监听型），随后回到页面顶部。"""

    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        for _ in range(_SCROLL_MAX_STEPS):
            at_end = await page.evaluate(_SCROLL_STEP_SCRIPT, _SCROLL_STEP_RATIO)
            if at_end:
                break
            await _pause(page, _SCROLL_PAUSE_MS)
    except Exception:  # noqa: BLE001 - 页面关闭/脚本被拒，按未触发继续
        return
    finally:
        try:
            await page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:  # noqa: BLE001 - 尽力回顶
            pass


async def _measure_document_height(page: Any, config: TaskConfig) -> int | None:
    """文档实际高度（封顶 max_full_page_screenshot_height），失败返回 None。"""

    try:
        raw = await page.evaluate(_DOCUMENT_HEIGHT_SCRIPT)
    except Exception:  # noqa: BLE001 - 页面关闭/脚本被拒
        return None
    try:
        height = int(raw)
    except (TypeError, ValueError):
        return None
    return max(1, min(height, config.max_full_page_screenshot_height))


async def _viewport_metrics(page: Any) -> int | None:
    """当前视口 CSS 高（dpr=1 几何链下即物理像素），失败返回 None。"""

    try:
        raw = await page.evaluate("() => window.innerHeight")
    except Exception:  # noqa: BLE001
        return None
    try:
        height = int(raw)
    except (TypeError, ValueError):
        return None
    return height if height > 0 else None


async def _wait_for_viewport(page: Any, target_height: int) -> None:
    """有界等待扩窗后的视口重排到位；超时按现状继续抓。"""

    deadline = asyncio.get_running_loop().time() + _SETTLE_TIMEOUT_SECONDS
    while True:
        height = await _viewport_metrics(page)
        if height is None or height >= target_height - _VIEWPORT_TOLERANCE_PX:
            return
        if asyncio.get_running_loop().time() >= deadline:
            logger.warning(
                "Long capture viewport settle timeout: innerHeight=%s target=%s",
                height,
                target_height,
            )
            return
        await asyncio.sleep(_SETTLE_POLL_SECONDS)


async def _wait_for_images(page: Any) -> None:
    """有界等待扩窗后进入视口的懒加载图片完成（IntersectionObserver 型）。"""

    deadline = asyncio.get_running_loop().time() + _SETTLE_TIMEOUT_SECONDS
    while True:
        try:
            pending = await page.evaluate(_PENDING_IMAGES_SCRIPT)
        except Exception:  # noqa: BLE001 - 页面关闭按加载完成处理
            return
        if not pending:
            return
        if asyncio.get_running_loop().time() >= deadline:
            logger.warning("Long capture image settle timeout: %s pending", pending)
            return
        await asyncio.sleep(_SETTLE_POLL_SECONDS)


async def _pause(page: Any, milliseconds: int) -> None:
    waiter = getattr(page, "wait_for_timeout", None)
    if callable(waiter):
        await waiter(milliseconds)
    else:
        await asyncio.sleep(milliseconds / 1000)


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:  # noqa: BLE001 - 窗口可能已销毁
        return None
    return (left, top, right, bottom)


def _set_window_geometry(
    hwnd: int,
    rect: tuple[int, int, int, int],
    new_height: int,
) -> None:
    import win32con
    import win32gui

    left, top, right, _bottom = rect
    win32gui.SetWindowPos(
        hwnd,
        None,
        left,
        top,
        right - left,
        new_height,
        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
    )


def _restore_window(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    """恢复扩窗前窗口几何；窗口已销毁时静默放过。"""

    try:
        _set_window_geometry(hwnd, rect, rect[3] - rect[1])
    except Exception:  # noqa: BLE001 - 恢复失败不影响已产出的证据图
        logger.warning("Unable to restore the browser window geometry.")


def _encode_jpeg(image: "Image.Image", quality: int) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _save_with_budget(
    image: "Image.Image",
    output_path: Path,
    config: TaskConfig,
) -> None:
    """按字节预算自适应编码：质量阶梯 → 缩放阶梯，兜底写最小一档。"""

    from PIL import Image

    budget = max(1, int(config.long_screenshot_max_bytes))
    start_quality = max(_QUALITY_FLOOR, min(config.long_page_jpeg_quality, 100))
    smallest: bytes | None = None
    for scale in _SCALE_LADDER:
        candidate = image
        if scale < 1.0:
            candidate = image.resize(
                (
                    max(1, round(image.width * scale)),
                    max(1, round(image.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        try:
            quality = start_quality
            while quality >= _QUALITY_FLOOR:
                encoded = _encode_jpeg(candidate, quality)
                if smallest is None or len(encoded) < len(smallest):
                    smallest = encoded
                if len(encoded) <= budget:
                    output_path.write_bytes(encoded)
                    return
                quality -= _QUALITY_STEP
        finally:
            if candidate is not image:
                candidate.close()
    if smallest is None:
        raise LongCaptureError("Unable to encode the long-page screenshot.")
    logger.warning(
        "Long screenshot exceeds the %d-byte budget even at the smallest "
        "ladder rung (%d bytes); keeping the smallest encoding.",
        budget,
        len(smallest),
    )
    output_path.write_bytes(smallest)


__all__ = [
    "LONG_PAGE_PLATFORMS",
    "LongCaptureError",
    "expand_and_capture",
    "handle_manual_long_capture",
    "is_long_page_platform",
]
