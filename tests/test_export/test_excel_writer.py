from pathlib import Path

import src.export.excel_writer as excel_writer_module
from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    TemplateRow,
    UrlTask,
)
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


def test_excel_writer_accepts_placeholder_row_without_screenshot() -> None:
    result = RecordResult(
        task=UrlTask(4, "https://item.jd.com/4.html", "https://item.jd.com/4.html"),
        status=RecordStatus.FAILED,
        route=RouteDecision("电商平台", "京东_京东商城_电商平台", "商家"),
    )
    row = TemplateRowMapper().map(result)

    ExcelTemplateWriter._validate_row(get_sheet_layout("电商平台"), row)


class _FakeCell:
    def __init__(self, *, locked: bool = True) -> None:
        self.Locked = locked
        self.Value: object | None = None


class _FakeDynamicSheet:
    def __init__(self) -> None:
        self.ProtectContents = True
        self._cells: dict[tuple[int, int], _FakeCell] = {}

    def Unprotect(self) -> None:
        self.ProtectContents = False

    def Cells(self, row: int, column: int) -> _FakeCell:
        return self._cells.setdefault((row, column), _FakeCell())


class _FakeRange:
    def __init__(self) -> None:
        self.Locked = False
        self.Value: object | None = None


class _FakeBulkSheet(_FakeDynamicSheet):
    def __init__(self) -> None:
        super().__init__()
        self.ProtectContents = False
        self.ranges: list[_FakeRange] = []

    def Range(self, _start: _FakeCell, _end: _FakeCell) -> _FakeRange:
        target = _FakeRange()
        self.ranges.append(target)
        return target


def test_excel_writer_extends_short_sheet_without_dropping_rows() -> None:
    layout = get_sheet_layout("电商平台")
    sheet = _FakeDynamicSheet()
    last_row = ExcelTemplateWriter._ensure_sheet_capacity(sheet, layout, 3)
    rows = [
        TemplateRow(
            "电商平台",
            evidence_id,
            {
                "A": f"https://item.jd.com/{evidence_id}.html",
                "B": "京东_京东商城_电商平台",
                "D": "商家",
            },
        )
        for evidence_id in range(1, 4)
    ]

    ExcelTemplateWriter._write_sheet_rows(sheet, layout, rows)

    assert last_row == 5
    assert sheet.ProtectContents is False
    assert sheet.Cells(5, 1).Value == "https://item.jd.com/3.html"


def test_excel_writer_batches_each_populated_column() -> None:
    layout = get_sheet_layout("电商平台")
    sheet = _FakeBulkSheet()
    rows = [
        TemplateRow(
            layout.name,
            evidence_id,
            {
                "A": f"https://item.jd.com/{evidence_id}.html",
                "B": "京东_京东商城_电商平台",
            },
        )
        for evidence_id in range(1, 4)
    ]

    ExcelTemplateWriter._write_sheet_rows(sheet, layout, rows)

    assert len(sheet.ranges) == 2
    assert sheet.ranges[0].Value == (
        ("https://item.jd.com/1.html",),
        ("https://item.jd.com/2.html",),
        ("https://item.jd.com/3.html",),
    )


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
