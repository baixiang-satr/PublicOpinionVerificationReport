from pathlib import Path

from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.domain.template_schema import SHEET_LAYOUTS
from src.services.review_session import ReviewSession
from src.services.sheet_display import attachment_names, row_values


def _record(evidence_id: int) -> RecordResult:
    task = UrlTask(
        evidence_id,
        f"https://example.test/p/{evidence_id}",
        f"https://example.test/p/{evidence_id}",
    )
    return RecordResult(
        task,
        RecordStatus.NEEDS_REVIEW,
        page=PageData(title="标题", content_text="正文", author_name="昵称"),
        route=RouteDecision("微博博客", "新浪_新浪微博_博客贴吧", "正文"),
        assets=AssetSet(),
    )


def _session(tmp_path: Path, records: list[RecordResult]) -> ReviewSession:
    return ReviewSession.from_records(tmp_path, records)


def test_attachment_names_fall_back_to_crawled_assets(tmp_path: Path) -> None:
    record = _record(1)
    record.assets.author_screenshot = Path("001主页.jpg")
    record.assets.extra_attachments = [Path("001_extra.png")]
    session = _session(tmp_path, [record])

    assert attachment_names(session, record) == ["001主页.jpg", "001_extra.png"]


def test_attachment_names_author_slot_prefers_manual_capture(tmp_path: Path) -> None:
    record = _record(1)
    record.assets.author_screenshot = Path("001主页.jpg")
    record.assets.extra_attachments = [Path("001_extra.png")]
    session = _session(tmp_path, [record])
    session.set_author_screenshot(1, "001_author_20260730.png")

    names = attachment_names(session, record)

    # 个人页槽位人工优先；额外附件槽位保留抓取值
    assert names == ["001_author_20260730.png", "001_extra.png"]


def test_attachment_names_extra_slot_prefers_manual_list(tmp_path: Path) -> None:
    record = _record(1)
    record.assets.author_screenshot = Path("001主页.jpg")
    record.assets.extra_attachments = [Path("001_extra.png")]
    session = _session(tmp_path, [record])
    session.set_attachments(1, ["001_manual_extra.png"])

    names = attachment_names(session, record)

    # 抓取的主页截图仍在个人页槽位；额外附件槽位被人工列表替换
    assert names == ["001主页.jpg", "001_manual_extra.png"]


def test_attachment_names_empty_without_any_assets(tmp_path: Path) -> None:
    record = _record(1)
    session = _session(tmp_path, [record])

    assert attachment_names(session, record) == []


def test_row_values_attachment_column_matches_slot_merge(tmp_path: Path) -> None:
    record = _record(1)
    session = _session(tmp_path, [record])
    session.set_author_screenshot(1, "001_author.png")
    session.set_attachments(1, ["001_extra_a.png", "001_extra_b.png"])
    layout = SHEET_LAYOUTS["微博博客"]

    values = row_values(session, record, layout)

    attachment_column = layout.attachment_column
    assert attachment_column is not None
    assert values[attachment_column] == "001_author.png,001_extra_a.png,001_extra_b.png"


def _video_record(evidence_id: int) -> RecordResult:
    task = UrlTask(
        evidence_id,
        f"https://v.douyin.com/p/{evidence_id}",
        f"https://v.douyin.com/p/{evidence_id}",
    )
    return RecordResult(
        task,
        RecordStatus.NEEDS_REVIEW,
        page=PageData(title="标题", content_text="正文", author_name="昵称", author_id="账号1"),
        route=RouteDecision("图文视频", "字节跳动_抖音_图文视频", "正文"),
        assets=AssetSet(),
    )


def test_swapped_sheet_primary_column_shows_homepage_and_attachment_shows_content(
    tmp_path: Path,
) -> None:
    """图文视频对调表：H 列=个人主页截图，I 列=内容页截图+额外附件。"""

    record = _video_record(1)
    record.assets.page_screenshot = Path("001.jpg")
    record.assets.author_screenshot = Path("001主页.jpg")
    record.assets.extra_attachments = [Path("001_extra.png")]
    session = _session(tmp_path, [record])
    layout = SHEET_LAYOUTS["图文视频"]

    assert attachment_names(session, record) == ["001.jpg", "001_extra.png"]

    values = row_values(session, record, layout)
    assert values["H"] == "001主页.jpg"
    assert values["I"] == "001.jpg,001_extra.png"


def test_swapped_sheet_content_slot_prefers_manual_capture(tmp_path: Path) -> None:
    record = _video_record(1)
    record.assets.page_screenshot = Path("001.jpg")
    record.assets.author_screenshot = Path("001主页.jpg")
    session = _session(tmp_path, [record])
    session.set_primary_screenshot(1, "001_content_manual.png")

    # 内容页槽位人工优先；主截图列仍是个人主页截图
    assert attachment_names(session, record) == ["001_content_manual.png"]
    assert session.primary_screenshot_name(record) == "001主页.jpg"
