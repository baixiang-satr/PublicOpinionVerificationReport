"""webui 序列化层测试：sheet 载荷与模板契约一致（离线，无浏览器/Excel）。"""
from __future__ import annotations

from pathlib import Path

from src.domain.models import RecordResult, RecordStatus, RouteDecision, UrlTask
from src.domain.template_schema import SHEET_LAYOUTS, SHEET_ORDER
from src.services.review_session import ReviewSession
from src.webui import serialize


def _record(eid: int, url: str, sheet: str, status: RecordStatus) -> RecordResult:
    return RecordResult(
        task=UrlTask(eid, url, url),
        status=status,
        route=RouteDecision(sheet_name=sheet, platform_value="", text_type="正文"),
    )


def _session(tmp_path: Path, records: list[RecordResult]) -> ReviewSession:
    return ReviewSession.from_records(tmp_path / "job", records)


def test_sheet_payload_covers_all_template_sheets(tmp_path: Path) -> None:
    session = _session(tmp_path, [])
    payload = serialize.sheet_payload(session)
    assert [sheet["name"] for sheet in payload] == list(SHEET_ORDER)
    for sheet in payload:
        layout = SHEET_LAYOUTS[sheet["name"]]
        assert len(sheet["columns"]) == layout.column_count
        assert sheet["rows"] == []
        assert sheet["manual_row_allowed"] == ("url" not in layout.field_columns)


def test_column_kinds_and_editability(tmp_path: Path) -> None:
    session = _session(tmp_path, [])
    payload = {sheet["name"]: sheet for sheet in serialize.sheet_payload(session)}
    video = payload["图文视频"]
    by_key = {column["key"]: column for column in video["columns"]}
    assert by_key["A"]["kind"] == "url"
    assert by_key["A"]["editable"] is False  # url 列不可编辑
    assert by_key["H"]["kind"] == "screenshot"
    assert by_key["I"]["kind"] == "attachment"
    assert by_key["C"]["editable"] is True  # 昵称可补录
    assert by_key["C"]["required"] is True
    # 下拉选项与模板完全一致
    assert tuple(by_key["D"]["choices"]) == SHEET_LAYOUTS["图文视频"].validation_values["D"]
    group = payload["群聊"]
    assert group["manual_row_allowed"] is True


def test_rows_carry_display_and_attention_state(tmp_path: Path) -> None:
    records = [
        _record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW),
        _record(2, "https://example.com/b", "群聊", RecordStatus.EXPORTED),
    ]
    session = _session(tmp_path, records)
    payload = {sheet["name"]: sheet for sheet in serialize.sheet_payload(session)}
    weibo_rows = payload["微博博客"]["rows"]
    assert len(weibo_rows) == 1
    row = weibo_rows[0]
    assert row["eid"] == 1
    assert row["cells"]["A"] == "https://example.com/a"
    assert row["status_text"] == "待补录"
    assert row["attention"] is True
    assert row["manual"] is False
    assert "截图" in row["missing"]  # 微博博客必有截图列缺失

    group_rows = payload["群聊"]["rows"]
    assert len(group_rows) == 1
    assert group_rows[0]["status_text"] == "成功"


def test_session_overview_and_row_delta(tmp_path: Path) -> None:
    records = [_record(1, "https://example.com/a", "微博博客", RecordStatus.NEEDS_REVIEW)]
    session = _session(tmp_path, records)
    overview = serialize.session_overview(session)
    assert overview is not None
    assert overview["total"] == 1
    assert overview["done"] == 0

    session.set_field(1, "author_name", "小明")
    delta = serialize.row_delta(session, 1)
    assert "昵称" not in delta["missing"]
    assert "截图" in delta["missing"]

    assert serialize.session_overview(None) is None
