"""后台浏览器窗口截图：PrintWindow 直抓窗口（含真实地址栏 URL）。

证据合规要求截图与最终 URL 同框。抓取浏览器窗口本体（标签栏 + 地址栏
+ 页面）走 Windows PrintWindow(PW_RENDERFULLCONTENT)：窗口被遮挡、在
后台、甚至移出屏幕（``background_crawl_browser`` 默认把抓取窗口放到
-32000,-32000）都能截到，用户全程无感。注入 DOM 横幅（SPA 个人页
``location.href`` 失真）与全屏 ImageGrab（依赖窗口前台可见）两种旧
方案均已废弃。

窗口定位：把页面标题临时改成唯一 token，EnumWindows 按窗口标题匹配
HWND，截完恢复原标题；token 每次截取唯一，并发多窗口互不干扰。
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.settings import TaskConfig

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

_PW_RENDERFULLCONTENT = 0x00000002
#: 窗口标题同步重试：document.title → 窗口标题有几十毫秒延迟。
FIND_WINDOW_RETRIES = 25
FIND_WINDOW_INTERVAL_SECONDS = 0.08
#: 标题恢复后等待标签栏重绘的时间，证据图应显示真实页标题而非 token。
TAB_STRIP_REPAINT_SECONDS = 0.15
_TITLE_TAG = "__POIR_CAPTURE_"

#: 窗口标题只反映活动标签；同平台 URL 并行后多条 URL 共享同一窗口的
#: 不同标签，并发抓拍会互切活动标签，让证据图带上错误的地址栏 URL。
#: 抓拍全程（打 token → 定位窗口 → 激活标签 → PrintWindow → 恢复）由
#: 该锁全局串行化；单次约 1 秒，且消除跨窗口并发的前台争抢。
_CAPTURE_LOCK = asyncio.Lock()
#: 渲染几何自检每进程只跑一次（首个证据截图时）。
_geometry_probe_done = False


class WindowCaptureError(RuntimeError):
    """Raised when the browser window cannot be captured in the background."""


async def _probe_geometry_once(page: Any, config: TaskConfig) -> None:
    """Warn when the live CSS geometry diverges from the pinned viewport.

    All automatic evidence captures funnel through
    ``capture_browser_window``, so one probe here covers the whole crawl.
    Mismatches (125%/150% display scaling, missing
    ``--force-device-scale-factor``) produce shifted, unusable screenshots
    on other machines — surface them in the log instead of silently.
    """

    global _geometry_probe_done
    if _geometry_probe_done:
        return
    _geometry_probe_done = True
    from src.screenshot.capture_diagnostics import probe_page_geometry

    geometry = await probe_page_geometry(page, config)
    if geometry.get("ok"):
        logger.info(
            "Capture geometry self-check passed: %sx%s dpr=%s",
            geometry.get("inner_width"),
            geometry.get("inner_height"),
            geometry.get("device_pixel_ratio"),
        )
        return
    for warning in geometry.get("warnings", ()):
        logger.warning("Capture geometry self-check: %s", warning)


async def capture_browser_window(
    page: Any,
    output_path: Path,
    config: TaskConfig,
    *,
    long_page: bool = False,
) -> None:
    """直抓页面所在浏览器窗口（含地址栏），按配置格式落盘。

    目标标签已是活动标签时零打扰；未激活时先激活再抓，并在结束后恢复
    用户的前台窗口。离屏/后台窗口无需可见。并发调用经 _CAPTURE_LOCK
    串行化，保证窗口标题 token 与活动标签不被其他抓拍干扰。
    ``long_page=True``（文字类平台内容页）先把窗口撑高到整页文档高再
    抓，产出带真实地址栏的整页长图；长图失败回退普通视口抓窗。
    """

    async with _CAPTURE_LOCK:
        await _capture_window_locked(page, output_path, config, long_page=long_page)


async def _capture_window_locked(
    page: Any,
    output_path: Path,
    config: TaskConfig,
    *,
    long_page: bool = False,
) -> None:
    await _probe_geometry_once(page, config)
    token = f"{_TITLE_TAG}{uuid.uuid4().hex[:12]}"
    try:
        original_title = await page.title()
    except Exception:  # noqa: BLE001 - 标题缺失不阻断截图
        original_title = ""
    try:
        await page.evaluate("(t) => { document.title = t; }", token)
    except Exception as error:  # noqa: BLE001
        raise WindowCaptureError(f"Unable to tag the capture window: {error}") from error
    previous_foreground = None
    try:
        hwnd = await _wait_for_window(token, retries=8)
        if hwnd is None:
            # 目标标签未激活（并发同窗口多标签）：激活后重查，结束恢复前台。
            previous_foreground = _foreground_window()
            await _activate_page_tab(page)
            hwnd = await _wait_for_window(token, retries=FIND_WINDOW_RETRIES)
        if hwnd is None:
            raise WindowCaptureError("Unable to locate the browser window for capture.")
    finally:
        # 先恢复真实标题再截图：证据图标签栏不得留下定位 token。
        try:
            await page.evaluate("(t) => { document.title = t; }", original_title)
        except Exception:  # noqa: BLE001 - 页面可能已关闭
            pass
    await asyncio.sleep(TAB_STRIP_REPAINT_SECONDS)
    try:
        if long_page:
            try:
                # 延迟导入：long_capture 在模块级引用本模块的 _print_window。
                from src.screenshot.long_capture import expand_and_capture

                await expand_and_capture(page, hwnd, output_path, config)
                return
            except Exception as error:  # noqa: BLE001 - 长图失败回退视口抓窗
                logger.warning(
                    "Long-page capture failed; falling back to viewport capture: %s",
                    error,
                )
        image = await asyncio.to_thread(_print_window, hwnd)
    finally:
        if previous_foreground:
            _restore_foreground(previous_foreground)
    try:
        await asyncio.to_thread(_save_image, image, output_path, config)
    finally:
        image.close()


async def _activate_page_tab(page: Any) -> None:
    bringer = getattr(page, "bring_to_front", None)
    if not callable(bringer):
        return
    try:
        await bringer()
    except Exception as error:  # noqa: BLE001 - 激活失败仍可尝试按标题找
        logger.warning("Unable to activate capture tab: %s", error)


async def _wait_for_window(token: str, *, retries: int) -> int | None:
    for attempt in range(max(1, retries)):
        hwnd = await asyncio.to_thread(_find_window_by_title, token)
        if hwnd is not None:
            return hwnd
        if attempt + 1 < retries:
            await asyncio.sleep(FIND_WINDOW_INTERVAL_SECONDS)
    return None


def _find_window_by_title(token: str) -> int | None:
    """顶层可见窗口中按标题子串匹配 HWND（token 唯一，先见先得）。"""

    import win32gui

    matches: list[int] = []

    def _collect(hwnd: int, _extra: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd) and token in win32gui.GetWindowText(hwnd):
            matches.append(hwnd)
        return True

    win32gui.EnumWindows(_collect, None)
    return matches[0] if matches else None


def _foreground_window() -> int | None:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return hwnd or None


def _restore_foreground(hwnd: int) -> None:
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - 恢复前台失败不影响截图结果
        pass


def _print_window(hwnd: int) -> "Image.Image":
    """PrintWindow(PW_RENDERFULLCONTENT) 抓取整窗（含非客户区/地址栏）。"""

    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)
    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    memory_dc.SelectObject(bitmap)
    try:
        ok = ctypes.windll.user32.PrintWindow(
            hwnd,
            memory_dc.GetSafeHdc(),
            _PW_RENDERFULLCONTENT,
        )
        if not ok:
            raise WindowCaptureError("PrintWindow failed for the browser window.")
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        frame = Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bits,
            "raw",
            "BGRX",
            0,
            1,
        )
        return frame.copy()  # 位图句柄销毁前拷贝出独立像素
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)


def _save_image(
    image: "Image.Image",
    output_path: Path,
    config: TaskConfig,
) -> None:
    if config.screenshot_format == "jpeg":
        image.convert("RGB").save(
            str(output_path),
            format="JPEG",
            quality=config.screenshot_jpeg_quality,
        )
    else:
        image.save(str(output_path), format="PNG")


__all__ = ["WindowCaptureError", "capture_browser_window"]
