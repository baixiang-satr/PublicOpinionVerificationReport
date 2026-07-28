from pathlib import Path

import src.export.excel_writer as excel_writer_module
from src.domain.models import AssetSet, PageData, RecordResult, RecordStatus, RouteDecision, UrlTask
from src.domain.template_schema import get_sheet_layout
from src.export.excel_writer import ExcelTemplateWriter, TemplateIntegrityError
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


def test_excel_writer_retries_transient_com_busy_error(monkeypatch) -> None:
    attempts = 0

    def flaky_operation(*_args: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TemplateIntegrityError(
                "Excel template operation failed: (-2147418111, '被呼叫方拒绝接收呼叫。')"
            )
        return "ok"

    monkeypatch.setattr(ExcelTemplateWriter, "_run_excel_once", staticmethod(flaky_operation))
    monkeypatch.setattr(excel_writer_module.time, "sleep", lambda _seconds: None)

    assert ExcelTemplateWriter._run_with_excel("inspect", Path("template.xlsx")) == "ok"
    assert attempts == 3


def test_excel_writer_does_not_retry_non_transient_integrity_error(monkeypatch) -> None:
    def invalid_template(*_args: object) -> None:
        raise TemplateIntegrityError("Header contract changed")

    monkeypatch.setattr(ExcelTemplateWriter, "_run_excel_once", staticmethod(invalid_template))
    monkeypatch.setattr(
        excel_writer_module.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("unexpected retry")),
    )

    try:
        ExcelTemplateWriter._run_with_excel("inspect", Path("template.xlsx"))
    except TemplateIntegrityError as error:
        assert "Header contract changed" in str(error)
    else:
        raise AssertionError("non-transient error was not raised")
