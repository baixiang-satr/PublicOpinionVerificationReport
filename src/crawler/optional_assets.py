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

    # Field recovery has priority over a potentially slow author homepage.
    # Each stage owns its timeout, so one optional asset can never starve the
    # next stage in a multi-URL batch.
    ocr_timeout = max(
        0.05,
        min(
            75.0,
            config.ocr_worker_timeout_seconds + 20.0,
            config.page_processing_timeout_seconds * 0.40,
        ),
    )
    try:
        async with asyncio.timeout(ocr_timeout):
            await _collect_ocr_assets(
                config=config,
                asset_collector=asset_collector,
                ocr_pipeline=ocr_pipeline,
                page=page,
                result=result,
                output_dir=output_dir,
                cancel_event=cancel_event,
            )
    except TimeoutError:
        result.errors.append(
            TaskError(
                "ocr",
                "OCR_TIMEOUT",
                "正文图片 OCR 超时；正文和主截图已保留",
                retryable=True,
            )
        )

    if not result.page.author_url:
        return
    author_timeout = max(
        0.05,
        min(90.0, config.page_processing_timeout_seconds * 0.35),
    )
    try:
        async with asyncio.timeout(author_timeout):
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
    except TimeoutError:
        result.errors.append(
            TaskError(
                "author_screenshot",
                "AUTHOR_SCREENSHOT_TIMEOUT",
                "作者主页截图超时；正文和主截图已保留",
                retryable=True,
            )
        )


async def _collect_ocr_assets(
    *,
    config: TaskConfig,
    asset_collector: Any,
    ocr_pipeline: Any,
    page: Any,
    result: RecordResult,
    output_dir: Path,
    cancel_event: asyncio.Event,
) -> None:
    """Collect and consume temporary body images for OCR."""

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
