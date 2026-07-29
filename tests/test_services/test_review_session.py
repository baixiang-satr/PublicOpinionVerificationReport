from pathlib import Path

from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.services.manual_assets import MANUAL_ASSETS_DIR_NAME, stage_manual_assets
from src.services.review_session import ReviewSession


def _record(
    evidence_id: int,
    *,
    status: RecordStatus = RecordStatus.NEEDS_REVIEW,
    sheet: str = "微博博客",
    title: str | None = None,
    content: str | None = None,
    author: str | None = None,
) -> RecordResult:
    task = UrlTask(
        evidence_id,
        f"https://example.test/p/{evidence_id}",
        f"https://example.test/p/{evidence_id}",
    )
    return RecordResult(
        task,
        status,
        page=PageData(title=title, content_text=content, author_name=author),
        route=RouteDecision(sheet, "新浪_新浪微博_博客贴吧", "正文"),
        assets=AssetSet(),
    )


def _session(tmp_path: Path, records: list[RecordResult]) -> ReviewSession:
    return ReviewSession.from_records(tmp_path, records)


def test_field_views_follow_sheet_column_order(tmp_path: Path) -> None:
    record = _record(1, title="标题", content="正文", author="昵称")
    session = _session(tmp_path, [record])
    views = session.field_views(1)
    fields = [view.field for view in views]
    # 微博博客 columns: url A, author_name B, platform C, text_type D,
    # published_at E, content F — editable fields keep that order.
    assert fields == ["author_name", "text_type", "published_at", "content"]
    labels = {view.field: view.label for view in views}
    assert labels["author_name"] == "昵称"
    assert labels["text_type"] == "文本类型"
    assert labels["content"] == "信息内容"
    text_type_view = next(view for view in views if view.field == "text_type")
    assert text_type_view.choices == ("正文", "评论回复")


def test_effective_value_manual_wins_and_marks_source(tmp_path: Path) -> None:
    record = _record(1, author="抓取昵称")
    session = _session(tmp_path, [record])
    session.set_field(1, "author_name", "人工昵称")
    views = {view.field: view for view in session.field_views(1)}
    author_view = views["author_name"]
    assert author_view.value == "人工昵称"
    assert author_view.source == "manual"
    assert author_view.source_text == "人工"
    assert not author_view.missing


def test_missing_labels_cover_required_fields_and_screenshot(tmp_path: Path) -> None:
    record = _record(1)  # nothing crawled
    session = _session(tmp_path, [record])
    missing = session.missing_labels(record, None)
    assert "昵称" in missing
    assert "信息内容" in missing
    assert "截图" in missing
    assert "文本类型" not in missing  # has route default
    summary = session.summary_for(record)
    assert summary.needs_attention


def test_missing_labels_satisfied_by_override_and_screenshot(tmp_path: Path) -> None:
    record = _record(1)
    session = _session(tmp_path, [record])
    session.set_field(1, "author_name", "人工昵称")
    session.set_field(1, "content", "人工正文")
    session.set_primary_screenshot(1, "001_manual.png")
    assert session.missing_labels(record, session.get_override(1)) == ()
    summary = session.summary_for(record)
    assert not summary.needs_attention
    assert summary.has_override
    done, total = session.completion_counts()
    assert (done, total) == (1, 1)


def test_next_attention_id_wraps_and_skips_complete(tmp_path: Path) -> None:
    incomplete = _record(1)
    complete = _record(
        2,
        status=RecordStatus.ASSETS_READY,
        title="t",
        content="c",
        author="a",
    )
    session = _session(tmp_path, [incomplete, complete])
    session.set_primary_screenshot(2, "002.png")
    assert session.next_attention_id(1) is None  # only #1 needs attention
    assert session.next_attention_id(2) == 1
    assert session.next_attention_id(1, backwards=True) is None


def test_primary_screenshot_path_prefers_manual_assets(tmp_path: Path) -> None:
    record = _record(1)
    session = _session(tmp_path, [record])
    session.set_primary_screenshot(1, "shot.png")
    assert session.primary_screenshot_path(record) is None
    manual_dir = session.manual_assets_dir()
    manual_dir.mkdir(parents=True)
    capture = manual_dir / "shot.png"
    capture.write_bytes(b"png")
    assert session.primary_screenshot_path(record) == capture


def test_stage_manual_assets_copies_and_audits_missing(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    template_dir = tmp_path / "staging" / "template"
    (job_dir / MANUAL_ASSETS_DIR_NAME).mkdir(parents=True)
    (job_dir / MANUAL_ASSETS_DIR_NAME / "shot.png").write_bytes(b"png")
    template_dir.mkdir(parents=True)

    with_manual = _record(1)
    with_manual.assets.page_screenshot = Path("shot.png")
    with_manual.assets.extra_attachments = [Path("shot.png"), Path("gone.png")]

    staged = stage_manual_assets(job_dir, template_dir, [with_manual])

    assert staged == 2
    assert with_manual.assets.page_screenshot == template_dir / "shot.png"
    assert (template_dir / "shot.png").read_bytes() == b"png"
    assert with_manual.assets.extra_attachments == [template_dir / "shot.png"]
    assert any(
        error.code == "MANUAL_ASSET_MISSING" for error in with_manual.errors
    )


def test_stage_manual_assets_keeps_existing_staged_files(tmp_path: Path) -> None:
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    existing = template_dir / "001.jpg"
    existing.write_bytes(b"jpg")
    record = _record(1)
    record.assets.page_screenshot = existing

    staged = stage_manual_assets(tmp_path, template_dir, [record])

    assert record.assets.page_screenshot == existing
    assert staged == 0
