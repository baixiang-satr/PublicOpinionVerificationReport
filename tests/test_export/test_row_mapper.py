from datetime import datetime
from pathlib import Path

from src.crawler.platform_router import PlatformRouter
from src.domain.models import AssetSet, PageData, RecordResult, RecordStatus, RouteDecision, UrlTask
from src.export.row_mapper import TemplateRowMapper


def test_row_mapper_builds_a_public_account_template_row() -> None:
    result = RecordResult(
        task=UrlTask(1, "https://example.com/article", "https://example.com/article"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("公众号", "微信-公众号", "正文"),
        page=PageData(
            final_url="https://example.com/article",
            title="文章标题",
            content_summary="正文摘要",
            author_id="wx-account",
            author_name="公众号名称",
            published_at=datetime(2026, 7, 14, 18, 48),
        ),
        assets=AssetSet(page_screenshot=Path("001.jpg"), author_screenshot=Path("001主页.jpg")),
    )

    row = TemplateRowMapper().map(result)

    assert row.sheet_name == "公众号"
    assert row.values_by_column["J"] == "001.jpg"
    assert row.values_by_column["K"] == "001主页.jpg"
    assert row.values_by_column["I"] == datetime(2026, 7, 14, 18, 48)


def test_row_mapper_keeps_partial_record_and_leaves_unknown_fields_blank() -> None:
    result = RecordResult(
        task=UrlTask(2, "https://www.zhihu.com/question/2", "https://www.zhihu.com/question/2"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
        page=PageData(title="只抓到了标题"),
        assets=AssetSet(page_screenshot=Path("002.jpg")),
    )

    row = TemplateRowMapper().map(result)

    assert row.values_by_column["A"] == "https://www.zhihu.com/question/2"
    assert row.values_by_column["C"] == "知乎_知乎_博客贴吧"
    assert row.values_by_column["G"] == "002.jpg"
    assert "B" not in row.values_by_column
    assert row.values_by_column["F"] == "【标题】只抓到了标题"


def test_row_mapper_preserves_distinct_title_inside_content_when_sheet_has_no_title_column() -> None:
    result = RecordResult(
        task=UrlTask(3, "https://www.zhihu.com/question/3", "https://www.zhihu.com/question/3"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
        page=PageData(title="问题标题", content_summary="问题正文"),
        assets=AssetSet(page_screenshot=Path("003.jpg")),
    )

    row = TemplateRowMapper().map(result)

    assert row.values_by_column["F"] == "【标题】问题标题\n【正文】\n问题正文"


def test_row_mapper_does_not_duplicate_a_title_already_leading_the_body() -> None:
    result = RecordResult(
        task=UrlTask(3, "https://www.zhihu.com/question/3", "https://www.zhihu.com/question/3"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
        page=PageData(title="问题标题", content_summary="问题标题\n问题正文详情"),
        assets=AssetSet(page_screenshot=Path("003.jpg")),
    )

    row = TemplateRowMapper().map(result)

    assert row.values_by_column["F"] == "问题标题\n问题正文详情"


def test_row_mapper_accepts_commerce_product_as_merchant() -> None:
    page = PageData(
        final_url="https://item.jd.com/100.html",
        title="商品标题",
        content_summary="商品描述",
        store_name="示例店铺",
    )
    route = PlatformRouter().route(page.final_url, page)
    assert route is not None
    result = RecordResult(
        task=UrlTask(3, page.final_url, page.final_url),
        status=RecordStatus.READY_FOR_EXPORT,
        route=route,
        page=page,
        assets=AssetSet(page_screenshot=Path("003.jpg")),
    )

    row = TemplateRowMapper().map(result)

    assert row.values_by_column["D"] == "商家"
    assert row.values_by_column["F"] == "示例店铺"


def test_row_mapper_preserves_failed_record_without_a_screenshot() -> None:
    result = RecordResult(
        task=UrlTask(
            4,
            "https://item.jd.com/100.html?from=input",
            "https://item.jd.com/100.html?from=input",
        ),
        status=RecordStatus.FAILED,
        route=RouteDecision("电商平台", "京东_京东商城_电商平台", "商家"),
        page=PageData(final_url="https://passport.jd.com/login"),
    )

    row = TemplateRowMapper().map(result)

    assert row.values_by_column["A"] == "https://item.jd.com/100.html?from=input"
    assert row.values_by_column["B"] == "京东_京东商城_电商平台"
    assert "G" not in row.values_by_column
    assert row.primary_screenshot_name is None
    assert row.all_asset_names() == ()


def test_row_mapper_exports_full_content_instead_of_the_short_summary() -> None:
    full_content = "完整正文" * 1_000
    result = RecordResult(
        task=UrlTask(5, "https://weibo.com/5", "https://weibo.com/5"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("微博博客", "新浪_新浪微博_博客贴吧", "正文"),
        page=PageData(
            title="标题",
            content_text=full_content,
            content_summary=full_content[:2_000],
        ),
    )

    row = TemplateRowMapper().map(result)

    expected = f"【标题】标题\n【正文】\n{full_content}"
    assert row.values_by_column["F"] == expected
    assert result.page.exported_content_chars == len(expected)
    assert not result.page.summary_truncated


def test_row_mapper_marks_only_the_excel_safety_truncation() -> None:
    result = RecordResult(
        task=UrlTask(6, "https://weibo.com/6", "https://weibo.com/6"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("微博博客", "新浪_新浪微博_博客贴吧", "正文"),
        page=PageData(content_text="字" * 100),
    )

    row = TemplateRowMapper(export_content_max_chars=32).map(result)

    assert row.values_by_column["F"] == "字" * 32
    assert result.page.original_content_chars == 100
    assert result.page.exported_content_chars == 32
    assert [error.code for error in result.errors] == [
        "CONTENT_TRUNCATED_FOR_EXCEL"
    ]
