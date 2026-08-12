"""全屏截图中的地址栏 URL 行不得混入 OCR 回填的正文/字段。"""

from src.crawler.screenshot_field_recovery import (
    recover_fields_from_ocr_text,
    strip_banner_lines,
)
from src.domain.models import PageData


def test_strip_banner_lines_removes_bare_url_lines() -> None:
    text = (
        "https://www.douyin.com/video/123\n"
        "正文第一行\n"
        "  https://example.com/x  \n"
        "正文第二行"
    )

    assert strip_banner_lines(text) == "正文第一行\n正文第二行"


def test_ocr_recovery_excludes_banner_url_but_keeps_content_and_time() -> None:
    page = PageData()

    recover_fields_from_ocr_text(
        page,
        "https://www.douyin.com/video/123456\n正文内容在这里\n2026-08-01 10:20",
        summary_max_chars=2_000,
    )

    assert page.content_text is not None
    assert "douyin.com" not in page.content_text
    assert "正文内容在这里" in page.content_text
    assert page.published_at is not None  # 时间行不受影响


def test_banner_only_ocr_text_recovers_nothing() -> None:
    page = PageData()

    recover_fields_from_ocr_text(
        page,
        "https://www.douyin.com/video/123456",
        summary_max_chars=2_000,
    )

    assert page.content_text is None
    assert page.published_at is None
