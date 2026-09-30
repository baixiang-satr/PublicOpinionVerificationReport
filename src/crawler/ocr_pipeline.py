"""Cancellable OCR orchestration for content images and evidence screenshots."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.config.settings import TaskConfig
from src.crawler.content_classifier import (
    apply_image_ocr,
    initialize_content_kind,
)
from src.crawler.screenshot_field_recovery import (
    needs_screenshot_field_recovery,
    recover_fields_from_ocr_text,
)
from src.domain.models import OcrStatus, PageData, TaskError
from src.ocr.client import OcrCancelled, OcrClient


class OcrPipeline:
    def __init__(
        self,
        config: TaskConfig,
        client: OcrClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or OcrClient.from_config(config)

    async def process_content_images(
        self,
        page: PageData,
        paths: list[Path],
        cancel_event: asyncio.Event,
    ) -> list[TaskError]:
        if not self._config.ocr_enabled or not paths:
            initialize_content_kind(
                page,
                summary_max_chars=self._config.summary_max_chars,
            )
            return []
        result = await self._recognize(paths, cancel_event)
        apply_image_ocr(
            page,
            result,
            summary_max_chars=self._config.summary_max_chars,
        )
        return _ocr_errors(result.status, result.error, image_only=True)

    async def recover_screenshot_fields(
        self,
        page: PageData,
        screenshot: Path,
        cancel_event: asyncio.Event,
    ) -> list[TaskError]:
        if (
            not self._config.ocr_enabled
            or not needs_screenshot_field_recovery(page)
        ):
            initialize_content_kind(
                page,
                summary_max_chars=self._config.summary_max_chars,
            )
            return []
        source = _crop_tall_screenshot(screenshot)
        try:
            result = await self._recognize([source], cancel_event)
        finally:
            if source != screenshot:
                source.unlink(missing_ok=True)
        if result.status == OcrStatus.SUCCESS and result.text:
            recover_fields_from_ocr_text(
                page,
                result.text,
                summary_max_chars=self._config.summary_max_chars,
            )
            if page.ocr_status == OcrStatus.NOT_RUN:
                page.ocr_status = result.status
                page.ocr_image_count = 1
                page.ocr_text_image_count = result.text_image_count
                page.ocr_text = result.text
        initialize_content_kind(
            page,
            summary_max_chars=self._config.summary_max_chars,
        )
        return _ocr_errors(result.status, result.error, image_only=False)

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)

    async def _recognize(
        self,
        paths: list[Path],
        cancel_event: asyncio.Event,
    ):
        try:
            return await asyncio.to_thread(
                self._client.recognize,
                paths,
                confidence_threshold=self._config.ocr_confidence_threshold,
                cancelled=cancel_event.is_set,
            )
        except OcrCancelled as error:
            raise asyncio.CancelledError from error


#: 整页长图只取顶部供 OCR：标题/作者/时间字段集中在文章顶部，
#: 对上万像素的长图整图识别会显著拖慢 RapidOCR 子进程。
_TALL_SCREENSHOT_OCR_CROP = 4_096


def _crop_tall_screenshot(screenshot: Path) -> Path:
    """超高长图裁顶部临时图供 OCR；不超过阈值或探测失败返回原图。"""

    try:
        from PIL import Image

        with Image.open(screenshot) as opened:
            if opened.height <= _TALL_SCREENSHOT_OCR_CROP:
                return screenshot
            top = opened.convert("RGB").crop(
                (0, 0, opened.width, _TALL_SCREENSHOT_OCR_CROP)
            )
    except Exception:  # noqa: BLE001 - 探测失败按原图识别
        return screenshot
    temp_path = screenshot.with_name(f"{screenshot.stem}.ocr-top.jpg")
    try:
        top.save(str(temp_path), format="JPEG", quality=85)
    finally:
        top.close()
    return temp_path


def _ocr_errors(
    status: OcrStatus,
    message: str,
    *,
    image_only: bool,
) -> list[TaskError]:
    if status == OcrStatus.SUCCESS:
        return []
    if status == OcrStatus.NO_TEXT:
        if not image_only:
            return []
        return [
            TaskError(
                "ocr",
                "IMAGE_ONLY_NO_TEXT",
                "正文图片 OCR 已执行，图片中未识别到文字",
                retryable=False,
            )
        ]
    code = {
        OcrStatus.UNAVAILABLE: "OCR_UNAVAILABLE",
        OcrStatus.TIMEOUT: "OCR_TIMEOUT",
        OcrStatus.FAILED: "OCR_FAILED",
    }.get(status)
    if code is None:
        return []
    return [
        TaskError(
            "ocr",
            code,
            message or f"OCR 状态：{status.value}",
            retryable=status in {OcrStatus.TIMEOUT, OcrStatus.FAILED},
        )
    ]
