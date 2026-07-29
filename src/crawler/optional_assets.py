"""Optional enrichment assets: author-home capture and body-image OCR inputs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.domain.models import RecordResult, TaskError
from src.screenshot.author_asset import capture_author_home_asset


logger = logging.getLogger(__name__)


async def collect_optional_assets(
    *,
    config: TaskConfig,
    author_shooter: Any,
    asset_collector: Any,
    ocr_pipeline: Any,
    page: Any,
    result: RecordResult,
    output_dir: Path,
    cancel_event: asyncio.Event,
) -> None:
    """Collect optional assets; failures never fail the main record."""

    if result.page.author_url:
        asset, error = await capture_author_home_asset(
            author_shooter,
            page,
            result,
            output_dir,
            cancel_event,
        )
        result.assets.author_screenshot = asset
        if error is not None:
            result.errors.append(error)

    # Body images are temporary OCR inputs, including mixed text/image
    # posts. They never become delivery attachments.
    if not config.ocr_enabled or not result.page.image_urls:
        result.errors.extend(
            await ocr_pipeline.process_content_images(
                result.page,
                [],
                cancel_event,
            )
        )
        return
    collected_files: list[Path] = []
    try:
        collected = await asset_collector.collect(
            page,
            result.page.image_urls,
            result.task.evidence_id,
            output_dir,
            cancel_event,
        )
        collected_files.extend(collected.files)
        result.errors.extend(collected.errors)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        result.errors.append(
            TaskError(
                "image_download",
                "ASSET_COLLECTION_FAILED",
                str(error),
                retryable=False,
            )
        )
        return

    try:
        _raise_if_cancelled(cancel_event)
        result.errors.extend(
            await ocr_pipeline.process_content_images(
                result.page,
                collected_files,
                cancel_event,
            )
        )
    finally:
        for path in collected_files:
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                logger.warning(
                    "Unable to remove temporary OCR image %s: %s",
                    path,
                    error,
                )


def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
    if cancel_event.is_set():
        raise asyncio.CancelledError
