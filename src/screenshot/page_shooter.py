"""Stable evidence-ID window screenshots written directly to the staging root."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.crawler.platform_catalog import find_platform
from src.screenshot.capture_auth import (
    GuestCaptureError,
    require_authenticated_capture,
)
from src.screenshot.capture_ready import (
    PageScreenshotError,
    _is_visually_blank,
    _pause_playing_media,
    _raise_if_cancelled,
    hide_obstructive_login_overlays,
    wait_for_capture_ready,
)
from src.screenshot.long_capture import is_long_page_platform
from src.screenshot.page_layout import (
    align_page_for_capture,
)
from src.screenshot.page_layout import (
    page_dimensions as _page_dimensions,
)
from src.screenshot.window_capture import capture_browser_window
from src.utils.file_utils import UnsafeFileNameError, require_safe_file_name


class PageShooter:
    def __init__(self, config: TaskConfig) -> None:
        self._config = config

    async def capture(
        self,
        page: Any,
        evidence_id: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
        *,
        definition: Any = None,
    ) -> Path:
        return await self.capture_named(
            page,
            f"{evidence_id:03d}",
            output_dir,
            cancel_event,
            definition=definition,
            allow_long_page=True,
        )

    async def capture_named(
        self,
        page: Any,
        file_stem: str,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
        *,
        definition: Any = None,
        focus_selectors: tuple[str, ...] = (),
        focus_texts: tuple[str, ...] = (),
        require_alignment: bool = True,
        allow_long_page: bool = False,
    ) -> Path:
        """直抓浏览器窗口（含地址栏 URL，窗口无需前台可见）。

        就绪、认证、弹窗遮挡、媒体暂停与内容对齐等证据门槛保持不变；
        对齐通过后不再 ``page.screenshot``，而是用 PrintWindow 抓取页面
        所在浏览器窗口本体——标签栏 + 地址栏 + 页面同框，最终 URL 直接
        进入证据图，窗口被遮挡/离屏（background_crawl_browser）也能截，
        用户无感。``require_alignment`` 为 False 时，对齐失败不再报错而
        直接截取当前视口（仅限身份已核验的作者主页等场景作为兜底）。
        ``allow_long_page`` 为 True（内容页入口）且平台属于文字类集合时，
        扩窗截取整页长图并统一落 JPEG（尺寸预算驱动）；作者主页等
        capture_named 直调方默认 False，行为不变。
        """
        _raise_if_cancelled(cancel_event)
        if definition is None:
            definition = find_platform(str(getattr(page, "url", "") or ""))
        long_page = allow_long_page and is_long_page_platform(
            getattr(definition, "key", None)
        )
        # 长图统一 JPEG（1MB 尺寸预算）；其余截图维持配置格式。
        extension = (
            "jpg"
            if (long_page or self._config.screenshot_format == "jpeg")
            else "png"
        )
        try:
            file_name = require_safe_file_name(f"{file_stem}.{extension}")
        except UnsafeFileNameError as error:
            raise PageScreenshotError(str(error)) from error
        output_path = Path(output_dir).resolve() / file_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await wait_for_capture_ready(
            page,
            definition,
            cancel_event,
            strict_platform_content=not bool(focus_selectors or focus_texts),
        )
        try:
            await require_authenticated_capture(page, definition)
        except GuestCaptureError as error:
            raise PageScreenshotError(str(error)) from error
        await hide_obstructive_login_overlays(page)
        await _pause_playing_media(page)
        is_douyin_video = bool(
            definition is not None
            and getattr(definition, "key", "") == "douyin"
            and "/video/" in str(getattr(page, "url", "") or "")
        )
        # 抖音视频页是视口应用，文档几何随播放器/推荐栏不断变化；只对
        # 水平偏移做取景修复，截图始终是当前视口的全屏画面。
        if is_douyin_video:
            dimensions = await _page_dimensions(
                page,
                definition,
                (*focus_selectors, "video", "[class*='player']"),
                focus_texts,
            )
            if (
                dimensions is not None
                and dimensions["needs_horizontal_alignment"]
            ):
                aligned = await align_page_for_capture(
                    page,
                    definition=definition,
                    focus_selectors=(
                        *focus_selectors,
                        "video",
                        "[class*='player']",
                    ),
                    focus_texts=focus_texts,
                )
                if not aligned:
                    raise PageScreenshotError(
                        "Target content could not be framed completely in the viewport."
                    )
        else:
            dimensions = await _page_dimensions(
                page,
                definition,
                focus_selectors,
                focus_texts,
            )
            has_horizontal_overflow = bool(
                dimensions is not None
                and dimensions["document_width"]
                > dimensions["viewport_width"] + 32
            )
            needs_horizontal_alignment = bool(
                dimensions is not None
                and dimensions["needs_horizontal_alignment"]
            )
            if (
                has_horizontal_overflow
                or needs_horizontal_alignment
                or focus_selectors
                or focus_texts
            ):
                aligned = await align_page_for_capture(
                    page,
                    definition=definition,
                    focus_selectors=focus_selectors,
                    focus_texts=focus_texts,
                )
                if not aligned and (
                    needs_horizontal_alignment or focus_selectors or focus_texts
                ) and require_alignment:
                    raise PageScreenshotError(
                        "Target content could not be framed completely in the viewport."
                    )
        # 对齐完成后直抓窗口：地址栏 URL 即最终 URL，可溯源且不可篡改。
        # 文字类平台内容页（long_page）先扩窗到整页再抓，产出长图。
        try:
            await capture_browser_window(
                page,
                output_path,
                self._config,
                long_page=long_page,
            )
        except Exception as error:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(f"Unable to capture screenshot: {error}") from error
        _raise_if_cancelled(cancel_event)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError("Window capture produced an empty file.")
        if _is_visually_blank(output_path):
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(
                "Screenshot is blank or near-uniform and is not usable as evidence."
            )
        return output_path
