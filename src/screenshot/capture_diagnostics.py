"""Capture-environment diagnostics for cross-machine screenshot support.

Evidence screenshots must look identical on every machine.  The facts that
historically broke that promise — OS display scaling, monitor geometry,
process DPI awareness, the actual CSS viewport/devicePixelRatio — are
collected here so a failing machine can be diagnosed from its job folder
(``capture_environment.json``) without a support session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.settings import TaskConfig

logger = logging.getLogger(__name__)

#: Diagnostics payload written into every crawl job directory.
CAPTURE_ENVIRONMENT_FILE = "capture_environment.json"

_DPI_AWARENESS_NAMES = {
    0: "unaware",
    1: "system",
    2: "per-monitor",
    3: "per-monitor-v2",
    4: "unaware-gdi-scaled",
}


def collect_capture_environment() -> dict[str, Any]:
    """OS/display facts that change page layout across machines."""

    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "dpi_awareness": _process_dpi_awareness(),
        "monitors": _enumerate_monitors(),
    }


def _process_dpi_awareness() -> str:
    try:
        import ctypes

        shcore = ctypes.windll.shcore
        # Vista 起可用的兼容路径（user32 的 *Context 变体在部分系统缺失）。
        shcore.GetProcessDpiAwareness.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        )
        shcore.GetProcessDpiAwareness.restype = ctypes.c_long
        value = ctypes.c_int(-1)
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(value)) == 0:
            return _DPI_AWARENESS_NAMES.get(
                int(value.value),
                f"unknown({value.value})",
            )
    except Exception:  # noqa: BLE001 - 非 Windows 或 API 缺失
        pass
    return "unknown"


def _monitor_scale_percent(hmonitor: int) -> int | None:
    try:
        import ctypes

        shcore = ctypes.windll.shcore
        shcore.GetScaleFactorForMonitor.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        )
        shcore.GetScaleFactorForMonitor.restype = ctypes.c_long
        scale_x = ctypes.c_uint(0)
        scale_y = ctypes.c_uint(0)
        result = shcore.GetScaleFactorForMonitor(
            hmonitor,
            ctypes.byref(scale_x),
            ctypes.byref(scale_y),
        )
        if result == 0:  # S_OK
            return int(scale_x.value)
    except Exception:  # noqa: BLE001 - 尽力而为
        pass
    return None


def _enumerate_monitors() -> list[dict[str, Any]]:
    monitors: list[dict[str, Any]] = []
    try:
        import win32api
    except Exception:  # noqa: BLE001 - 非 Windows 环境返回空列表
        return monitors
    try:
        for hmonitor, _hdc, rect in win32api.EnumDisplayMonitors():
            left, top, right, bottom = (int(v) for v in rect)
            monitors.append(
                {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "width": right - left,
                    "height": bottom - top,
                    "scale_percent": _monitor_scale_percent(int(hmonitor)),
                    "primary": left == 0 and top == 0,
                }
            )
    except Exception:  # noqa: BLE001 - 尽力而为
        pass
    return monitors


async def probe_page_geometry(page: Any, config: "TaskConfig") -> dict[str, Any]:
    """Compare a live page's CSS geometry with the configured viewport.

    Returns ``{"ok": bool, "warnings": [...], ...}``; never raises — the
    probe is a self-check and must not break captures on odd pages.
    """

    result: dict[str, Any] = {"ok": False, "warnings": []}
    warnings: list[str] = result["warnings"]
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        warnings.append("页面对象不支持 evaluate，无法探测渲染几何。")
        return result
    try:
        raw = await evaluate(
            "() => ({innerWidth: window.innerWidth, innerHeight:"
            " window.innerHeight, devicePixelRatio: window.devicePixelRatio})"
        )
    except Exception as error:  # noqa: BLE001 - 页面可能已关闭
        warnings.append(f"渲染几何探测失败: {error}")
        return result
    if not isinstance(raw, dict):
        warnings.append("渲染几何探测返回异常，无法判定版式一致性。")
        return result
    inner_width = int(raw.get("innerWidth") or 0)
    inner_height = int(raw.get("innerHeight") or 0)
    dpr = float(raw.get("devicePixelRatio") or 0)
    if (
        inner_width != config.viewport_width
        or inner_height != config.viewport_height
    ):
        warnings.append(
            f"实际视口 {inner_width}x{inner_height} 与配置 "
            f"{config.viewport_width}x{config.viewport_height} 不一致；"
            "证据截图版式将与标准机器不同。"
        )
    if abs(dpr - 1.0) > 0.01:
        warnings.append(
            f"devicePixelRatio={dpr}（期望 1.0）；"
            "请确认 --force-device-scale-factor=1 已生效。"
        )
    result.update(
        {
            "ok": not warnings,
            "inner_width": inner_width,
            "inner_height": inner_height,
            "device_pixel_ratio": dpr,
        }
    )
    return result


def persist_capture_environment(
    job_dir: Path,
    *,
    config: "TaskConfig | None" = None,
    geometry: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write ``capture_environment.json``; diagnostics never break a job."""

    path = Path(job_dir) / CAPTURE_ENVIRONMENT_FILE
    try:
        payload: dict[str, Any] = {
            "collected_at": datetime.now().astimezone().isoformat(),
            "environment": collect_capture_environment(),
            "viewport": (
                {
                    "width": config.viewport_width,
                    "height": config.viewport_height,
                    "background_crawl_browser": config.background_crawl_browser,
                    "browser_channel": config.browser_channel,
                }
                if config is not None
                else None
            ),
            "geometry": geometry,
            "extra": extra,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as error:  # noqa: BLE001 - 诊断信息尽力而为
        logger.warning("Unable to persist capture environment: %s", error)
    return path


async def persist_capture_environment_async(
    job_dir: Path,
    *,
    config: TaskConfig | None = None,
) -> Path:
    """线程池落盘 ``capture_environment.json``：诊断 I/O 不阻塞抓取循环。"""

    return await asyncio.to_thread(persist_capture_environment, job_dir, config=config)


__all__ = [
    "CAPTURE_ENVIRONMENT_FILE",
    "collect_capture_environment",
    "persist_capture_environment",
    "persist_capture_environment_async",
    "probe_page_geometry",
]
