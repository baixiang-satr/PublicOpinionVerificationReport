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

#: 必须交付「已验证作者个人主页截图」的平台；拿不到就标待补录人工补截。
REQUIRED_AUTHOR_SCREENSHOT_PLATFORMS = frozenset(
    {
        "douyin",
        "toutiao",
        "kuaishou",
        "bilibili",
        "xiaohongshu",
        "weibo",
        "wechat_official",
    }
)


def author_screenshot_required_unmet(
    platform_key: str | None,
    result: RecordResult,
) -> bool:
    """必需平台的作者主页截图仍未交付（转待补录的依据）。"""

    return (
        platform_key in REQUIRED_AUTHOR_SCREENSHOT_PLATFORMS
        and result.assets.author_screenshot is None
    )


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
    platform_key: str | None = None,
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
        _append_author_required_error(platform_key, result)
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
            if (
                asset is None
                and platform_key in REQUIRED_AUTHOR_SCREENSHOT_PLATFORMS
                and not cancel_event.is_set()
            ):
                # 必需平台原地重试一次：首试常败于渲染未完成/验证页抖动。
                asset, retry_error = await capture_author_home_asset(
                    author_shooter,
                    page,
                    result,
                    output_dir,
                    cancel_event,
                )
                if asset is not None:
                    error = None
                elif retry_error is not None:
                    error = retry_error
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
    _append_author_required_error(platform_key, result)


def _append_author_required_error(
    platform_key: str | None,
    result: RecordResult,
) -> None:
    if not author_screenshot_required_unmet(platform_key, result):
        return
    if any(error.code == "AUTHOR_SCREENSHOT_REQUIRED" for error in result.errors):
        return
    result.errors.append(
        TaskError(
            "author_screenshot",
            "AUTHOR_SCREENSHOT_REQUIRED",
            "该平台必须交付已验证的作者个人主页截图；自动获取失败，"
            "请在补录阶段选中该行点「截取个人页」人工补截。",
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
