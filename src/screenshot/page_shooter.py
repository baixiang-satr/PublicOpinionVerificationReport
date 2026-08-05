"""Stable evidence-ID page screenshots written directly to the staging root."""

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
from src.screenshot.page_layout import (
    align_page_for_capture,
)
from src.screenshot.page_layout import (
    page_dimensions as _page_dimensions,
)
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
        clip_region: dict[str, int] | None = None,
        require_alignment: bool = True,
    ) -> Path:
        """Capture with an optional explicit document-coordinate clip.

        ``clip_region`` skips geometry alignment and clips the screenshot to
        the given page coordinates (profile-body containers, split-layout
        columns).  All other gates (readiness, authentication, overlay
        dismissal, blank-image rejection) still apply.  ``require_alignment``
        为 False 时，对齐失败不再报错而直接截取当前视口（仅限身份已核验的
        作者主页等场景作为兜底）。
        """
        _raise_if_cancelled(cancel_event)
        extension = "jpg" if self._config.screenshot_format == "jpeg" else "png"
        try:
            file_name = require_safe_file_name(f"{file_stem}.{extension}")
        except UnsafeFileNameError as error:
            raise PageScreenshotError(str(error)) from error
        output_path = Path(output_dir).resolve() / file_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "path": str(output_path),
            "type": self._config.screenshot_format,
            "full_page": self._config.full_page_screenshot,
            "animations": "disabled",
        }
        if definition is None:
            definition = find_platform(str(getattr(page, "url", "") or ""))
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
        if is_douyin_video:
            # Douyin video pages are viewport applications whose document
            # geometry keeps changing with the player/recommendation rail.
            # Full-page capture can wait indefinitely for that moving surface.
            # One stable viewport contains the video caption, author and
            # visible publish time and is the correct evidence moment.
            options["full_page"] = False
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
        is_long_page = False
        if clip_region is not None:
            # Caller-supplied DOM region (profile body container / split
            # column): the page was measured at its current scroll position,
            # so no alignment pass is needed and the region must not be
            # replaced by the full-page geometry branch below.
            options["full_page"] = False
            options["clip"] = dict(clip_region)
        elif self._config.full_page_screenshot and not is_douyin_video:
            dimensions = await _page_dimensions(
                page,
                definition,
                focus_selectors,
                focus_texts,
            )
            if dimensions is not None:
                is_long_page = (
                    dimensions["height"]
                    > self._config.max_full_page_screenshot_height
                )
                has_horizontal_overflow = (
                    dimensions["document_width"]
                    > dimensions["viewport_width"] + 32
                )
                needs_horizontal_alignment = bool(
                    dimensions["needs_horizontal_alignment"]
                )
            else:
                has_horizontal_overflow = False
                needs_horizontal_alignment = False
            if dimensions is not None and (
                is_long_page
                or has_horizontal_overflow
                or needs_horizontal_alignment
                or bool(focus_selectors)
                or bool(focus_texts)
            ):
                aligned = True
                if has_horizontal_overflow or needs_horizontal_alignment or focus_selectors or focus_texts:
                    aligned = await align_page_for_capture(
                        page,
                        definition=definition,
                        focus_selectors=focus_selectors,
                        focus_texts=focus_texts,
                    )
                options["full_page"] = False
                if not aligned and (needs_horizontal_alignment or focus_selectors or focus_texts):
                    if require_alignment:
                        raise PageScreenshotError(
                            "Target content could not be framed completely in the viewport."
                        )
                # A Playwright clip and a horizontally scrolled document use
                # different coordinate spaces on several Chromium builds.
                # Let the browser capture the current viewport after verified
                # alignment.  Only an unshifted long document uses a clip.
                if not (
                    has_horizontal_overflow
                    or needs_horizontal_alignment
                    or focus_selectors
                    or focus_texts
                ):
                    options["clip"] = {
                        "x": 0,
                        "y": 0,
                        "width": dimensions["viewport_width"],
                        "height": min(
                            dimensions["height"],
                            self._config.max_full_page_screenshot_height,
                        ),
                    }
        if self._config.screenshot_format == "jpeg":
            options["quality"] = (
                self._config.long_page_jpeg_quality
                if is_long_page
                else self._config.screenshot_jpeg_quality
            )
        try:
            await page.screenshot(**options)
        except Exception as error:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(f"Unable to capture screenshot: {error}") from error
        _raise_if_cancelled(cancel_event)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError("Playwright returned an empty screenshot.")
        if _is_visually_blank(output_path):
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(
                "Screenshot is blank or near-uniform and is not usable as evidence."
            )
        return output_path
