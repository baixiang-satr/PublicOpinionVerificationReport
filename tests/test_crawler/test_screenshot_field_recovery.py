from datetime import datetime
from pathlib import Path

from src.crawler.screenshot_field_recovery import (
    needs_screenshot_field_recovery,
    recover_fields_from_screenshot,
)
from src.domain.models import ExtractionSource, PageData


def test_recovers_noisy_title_and_missing_time_without_overwriting_content() -> None:
    page = PageData(
        title="Prefetch",
        content_text="已从接口提取的商品保障说明",
        content_summary="已从接口提取的商品保障说明",
    )

    recover_fields_from_screenshot(
        page,
        Path("013.jpg"),
        summary_max_chars=2_000,
        confidence_threshold=0.5,
        ocr=lambda *_args, **_kwargs: (
            "打开抖音APP\n"
            "Qi2五合一无线充电器全金属折叠升降平板笔记本电脑充电支架底座\n"
            "2026-07-18 12:30\n"
            "加入购物车"
        ),
    )

    assert page.title == "Qi2五合一无线充电器全金属折叠升降平板笔记本电脑充电支架底座"
    assert page.content_text == "已从接口提取的商品保障说明"
    assert page.published_at == datetime(2026, 7, 18, 12, 30).astimezone()
    assert page.field_sources["title"] == ExtractionSource.OCR
    assert page.field_sources["published_at"] == ExtractionSource.OCR


def test_recovers_missing_content_and_preserves_good_title() -> None:
    page = PageData(title="原始可信标题")

    recover_fields_from_screenshot(
        page,
        Path("001.jpg"),
        summary_max_chars=8,
        confidence_threshold=0.5,
        ocr=lambda *_args, **_kwargs: "原始可信标题\n截图中的完整正文",
    )

    assert page.title == "原始可信标题"
    assert page.content_text == "原始可信标题\n截图中的完整正文"
    assert page.content_summary == "原始可信标题\n截"
    assert page.summary_truncated


def test_mbd_video_landing_never_uses_shell_screenshot_as_field_source() -> None:
    page = PageData(
        final_url=(
            "https://mbd.baidu.com/newspage/data/videolanding"
            "?nid=sv_4426235232588179908"
        )
    )
    called = False

    def ocr(*_args, **_kwargs) -> str:
        nonlocal called
        called = True
        return "扫码下载百度APP\n搜最新资讯、看热门视频\n2026-07-20"

    assert needs_screenshot_field_recovery(page) is False
    recover_fields_from_screenshot(
        page,
        Path("001.jpg"),
        summary_max_chars=2_000,
        confidence_threshold=0.5,
        ocr=ocr,
    )

    assert called is False
    assert page.title is None
    assert page.content_text is None
    assert page.published_at is None
