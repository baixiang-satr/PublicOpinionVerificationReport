from datetime import datetime
from pathlib import Path

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
