from pathlib import Path

from src.crawler.content_classifier import (
    IMAGE_ONLY_NO_TEXT_MARKER,
    apply_image_ocr,
)
from src.domain.models import ContentKind, OcrStatus, PageData
from src.ocr.models import OcrBatchResult, OcrImageResult


def _batch(status: OcrStatus, *texts: str) -> OcrBatchResult:
    images = tuple(
        OcrImageResult(
            Path(f"{index}.png"),
            OcrStatus.SUCCESS if text else OcrStatus.NO_TEXT,
            text=text,
        )
        for index, text in enumerate(texts, start=1)
    )
    return OcrBatchResult(status, images)


def test_image_text_becomes_information_content() -> None:
    page = PageData(image_urls=["https://example.test/poster.png"])

    apply_image_ocr(
        page,
        _batch(OcrStatus.SUCCESS, "海报标题\n活动时间 8 月 1 日"),
        summary_max_chars=2_000,
    )

    assert page.content_text == "海报标题\n活动时间 8 月 1 日"
    assert page.content_kind == ContentKind.IMAGE_WITH_TEXT
    assert page.ocr_text_image_count == 1


def test_mixed_content_appends_only_novel_image_text() -> None:
    page = PageData(
        content_text="正文第一行\n已经存在",
        image_urls=["https://example.test/poster.png"],
    )

    apply_image_ocr(
        page,
        _batch(OcrStatus.SUCCESS, "已经存在\n图片新增说明"),
        summary_max_chars=2_000,
    )

    assert page.content_text == "正文第一行\n已经存在\n【图片文字】\n图片新增说明"
    assert page.content_kind == ContentKind.MIXED_TEXT_AND_IMAGE


def test_only_successful_no_text_ocr_emits_explicit_marker() -> None:
    page = PageData(image_urls=["https://example.test/photo.png"])

    apply_image_ocr(
        page,
        _batch(OcrStatus.NO_TEXT, ""),
        summary_max_chars=2_000,
    )

    assert page.content_text == IMAGE_ONLY_NO_TEXT_MARKER
    assert page.content_kind == ContentKind.IMAGE_WITHOUT_TEXT


def test_unavailable_ocr_is_not_misreported_as_no_text() -> None:
    page = PageData(image_urls=["https://example.test/photo.png"])
    result = OcrBatchResult.unavailable(
        [Path("photo.png")],
        "worker unavailable",
    )

    apply_image_ocr(page, result, summary_max_chars=2_000)

    assert page.content_text is None
    assert page.content_kind == ContentKind.UNKNOWN
    assert page.ocr_status == OcrStatus.UNAVAILABLE
