from pathlib import Path

from src.domain.models import AssetSet, PageData, RecordResult, RecordStatus, RouteDecision, UrlTask
from src.domain.template_schema import get_sheet_layout
from src.export.excel_writer import ExcelTemplateWriter
from src.export.row_mapper import TemplateRowMapper


def test_excel_writer_accepts_partial_row_with_primary_screenshot() -> None:
    result = RecordResult(
        task=UrlTask(3, "https://www.zhihu.com/question/3", "https://www.zhihu.com/question/3"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
        page=PageData(content_summary="只抓到了少量正文"),
        assets=AssetSet(page_screenshot=Path("003.jpg")),
    )
    row = TemplateRowMapper().map(result)

    ExcelTemplateWriter._validate_row(get_sheet_layout("微博博客"), row)
