"""Stable evidence-ID page screenshots written directly to the staging root."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.utils.file_utils import UnsafeFileNameError, require_safe_file_name


class PageScreenshotError(RuntimeError):
    """Raised when a primary page screenshot cannot be created."""


class PageShooter:
    def __init__(self, config: TaskConfig) -> None:
        self._config = config

    async def capture(
        self,
        page: Any,
        evidence_id: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
    ) -> Path:
        return await self.capture_named(
            page,
            f"{evidence_id:03d}",
            output_dir,
            cancel_event,
        )

    async def capture_named(
        self,
        page: Any,
        file_stem: str,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
    ) -> Path:
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
        if self._config.screenshot_format == "jpeg":
            options["quality"] = 85
        try:
            await page.screenshot(**options)
        except Exception as error:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError(f"Unable to capture screenshot: {error}") from error
        _raise_if_cancelled(cancel_event)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)
            raise PageScreenshotError("Playwright returned an empty screenshot.")
        return output_path


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
