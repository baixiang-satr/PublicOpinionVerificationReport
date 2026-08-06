from pathlib import Path

from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.services import job_records
from src.services.checkpoint_store import CheckpointStore
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
    assert fields == ["author_name", "platform", "text_type", "published_at", "content"]
    labels = {view.field: view.label for view in views}
    assert labels["author_name"] == "昵称"
    assert labels["text_type"] == "文本类型"
    assert labels["content"] == "信息内容"
    text_type_view = next(view for view in views if view.field == "text_type")
    assert text_type_view.choices == ("正文", "评论回复")
    platform_view = next(view for view in views if view.field == "platform")
    assert "新浪_新浪微博_博客贴吧" in platform_view.choices


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
    assert "主页截图" in missing  # 带链接记录必须有两张截图
    assert "文本类型" not in missing  # has route default
    summary = session.summary_for(record)
    assert summary.needs_attention


def test_missing_labels_satisfied_by_override_and_screenshot(tmp_path: Path) -> None:
    record = _record(1)
    session = _session(tmp_path, [record])
    session.set_field(1, "author_name", "人工昵称")
    session.set_field(1, "content", "人工正文")
    session.set_primary_screenshot(1, "001_manual.png")
    session.set_attachments(1, ["001主页_manual.png"])
    assert session.missing_labels(record, session.get_override(1)) == ()
    summary = session.summary_for(record)
    assert not summary.needs_attention
    assert summary.has_override
    done, total = session.completion_counts()
    assert (done, total) == (1, 1)


def test_homepage_screenshot_flag_cleared_by_crawled_asset(tmp_path: Path) -> None:
    record = _record(1, title="标题", content="正文", author="昵称")
    session = _session(tmp_path, [record])
    session.set_primary_screenshot(1, "001.jpg")
    missing = session.missing_labels(record, session.get_override(1))
    assert "主页截图" in missing
    record.assets.author_screenshot = Path("001主页.jpg")
    missing = session.missing_labels(record, session.get_override(1))
    assert "主页截图" not in missing
    assert missing == ()


def test_homepage_screenshot_flag_cleared_by_manual_attachment(tmp_path: Path) -> None:
    record = _record(1, title="标题", content="正文", author="昵称")
    session = _session(tmp_path, [record])
    session.set_primary_screenshot(1, "001.jpg")
    session.set_attachments(1, ["001主页_manual.png"])
    missing = session.missing_labels(record, session.get_override(1))
    assert "主页截图" not in missing
    assert missing == ()


def test_homepage_screenshot_flag_cleared_by_author_override(tmp_path: Path) -> None:
    record = _record(1, title="标题", content="正文", author="昵称")
    session = _session(tmp_path, [record])
    session.set_primary_screenshot(1, "001.jpg")
    session.set_author_screenshot(1, "001_author_20260730.png")
    missing = session.missing_labels(record, session.get_override(1))
    assert "主页截图" not in missing
    assert missing == ()


def test_author_screenshot_name_and_path_precedence(tmp_path: Path) -> None:
    record = _record(1)
    record.assets.author_screenshot = Path("001主页.jpg")
    session = _session(tmp_path, [record])
    # 无 override：回落到抓取资产
    assert session.author_screenshot_name(record) == "001主页.jpg"
    assert session.author_screenshot_path(record) is None  # 相对路径文件不存在
    # override 优先；文件在 manual_assets 中时可解析预览路径
    session.set_author_screenshot(1, "001_author.png")
    assert session.author_screenshot_name(record) == "001_author.png"
    assert session.author_screenshot_path(record) is None
    manual_dir = session.manual_assets_dir()
    manual_dir.mkdir(parents=True)
    capture = manual_dir / "001_author.png"
    capture.write_bytes(b"png")
    assert session.author_screenshot_path(record) == capture


def test_homepage_screenshot_not_required_for_manual_rows(tmp_path: Path) -> None:
    session = _session(tmp_path, [])
    record = session.add_manual_record("群聊")  # 无 URL 的手工行
    missing = session.missing_labels(record, session.get_override(record.task.evidence_id))
    assert "主页截图" not in missing


def test_next_attention_id_wraps_and_skips_complete(tmp_path: Path) -> None:
    incomplete = _record(1)
    complete = _record(
        2,
        status=RecordStatus.ASSETS_READY,
        title="t",
        content="c",
        author="a",
    )
    complete.assets.author_screenshot = Path("002主页.png")
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


def test_stage_manual_assets_stages_author_screenshot(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    template_dir = tmp_path / "staging" / "template"
    (job_dir / MANUAL_ASSETS_DIR_NAME).mkdir(parents=True)
    (job_dir / MANUAL_ASSETS_DIR_NAME / "001_author.png").write_bytes(b"png")
    template_dir.mkdir(parents=True)

    record = _record(1)
    record.assets.author_screenshot = Path("001_author.png")  # 人工相对名

    staged = stage_manual_assets(job_dir, template_dir, [record])

    assert staged == 1
    assert record.assets.author_screenshot == template_dir / "001_author.png"
    assert (template_dir / "001_author.png").read_bytes() == b"png"


def test_stage_manual_assets_reports_missing_author_screenshot(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    template_dir = tmp_path / "staging" / "template"
    (job_dir / MANUAL_ASSETS_DIR_NAME).mkdir(parents=True)
    (job_dir / MANUAL_ASSETS_DIR_NAME / "gone.png").write_bytes(b"png")
    template_dir.mkdir(parents=True)

    record = _record(1)
    record.assets.author_screenshot = Path("missing.png")

    staged = stage_manual_assets(job_dir, template_dir, [record])

    assert staged == 0
    assert record.assets.author_screenshot is None
    assert any(
        error.code == "MANUAL_ASSET_MISSING" for error in record.errors
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


def _video_record(evidence_id: int) -> RecordResult:
    """图文视频对调表记录（账号截图列=个人主页截图）。"""

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


def test_swapped_sheet_missing_labels_track_homepage_then_content(tmp_path: Path) -> None:
    record = _video_record(1)  # 两张截图都没有
    session = _session(tmp_path, [record])

    missing = session.missing_labels(record, None)
    assert "主页截图" in missing  # 账号截图列（主截图列）必须交付个人主页截图
    assert "内容页截图" in missing  # 内容页截图经其他文件名列交付
    assert "截图" not in missing

    # 只有个人主页截图时：主截图列满足，仍缺内容页截图
    record.assets.author_screenshot = Path("001主页.jpg")
    missing = session.missing_labels(record, None)
    assert "主页截图" not in missing
    assert "内容页截图" in missing

    # 两张齐全后无缺失
    record.assets.page_screenshot = Path("001.jpg")
    assert session.missing_labels(record, None) == ()


def test_swapped_sheet_content_slot_satisfied_by_manual_primary(tmp_path: Path) -> None:
    record = _video_record(1)
    record.assets.author_screenshot = Path("001主页.jpg")
    session = _session(tmp_path, [record])
    session.set_primary_screenshot(1, "001_content_manual.png")

    missing = session.missing_labels(record, session.get_override(1))
    assert "内容页截图" not in missing
    assert missing == ()


def test_swapped_sheet_primary_name_and_path_come_from_homepage_slot(tmp_path: Path) -> None:
    record = _video_record(1)
    record.assets.page_screenshot = Path("001.jpg")
    session = _session(tmp_path, [record])

    # 主截图列跟随个人主页槽位
    assert session.primary_screenshot_name(record) is None
    session.set_author_screenshot(1, "001_author_manual.png")
    assert session.primary_screenshot_name(record) == "001_author_manual.png"
    # 内容页槽位不受人工主页影响
    assert session.content_screenshot_name(record) == "001.jpg"


def test_remove_record_deletes_crawled_row_with_screenshots(tmp_path: Path) -> None:
    """带 URL 的抓取行可删除；截图/判定 sidecar/人工资产/断点一并清理。"""

    record = _record(1)
    (tmp_path / "001.jpg").write_bytes(b"jpg")
    (tmp_path / "001主页.jpg").write_bytes(b"jpg")
    (tmp_path / "001主页.decision.json").write_text("{}", encoding="utf-8")
    record.assets.page_screenshot = tmp_path / "001.jpg"
    record.assets.author_screenshot = tmp_path / "001主页.jpg"
    manual_dir = tmp_path / MANUAL_ASSETS_DIR_NAME
    manual_dir.mkdir()
    (manual_dir / "001_extra.png").write_bytes(b"png")
    session = _session(tmp_path, [record])
    session.set_field(1, "author_name", "人工昵称")
    session.set_attachments(1, ["001_extra.png"])
    job_records.write_checkpoint(tmp_path, tmp_path.name, [record])

    assert session.remove_record(1) is True

    assert session.evidence_ids() == []
    assert session.get_override(1) is None
    assert not (tmp_path / "001.jpg").exists()
    assert not (tmp_path / "001主页.jpg").exists()
    assert not (tmp_path / "001主页.decision.json").exists()
    assert not (manual_dir / "001_extra.png").exists()
    snapshot = CheckpointStore.load(tmp_path / "job_checkpoint.json")
    assert list(snapshot.records) == []


def test_remove_record_keeps_sibling_rows_and_files(tmp_path: Path) -> None:
    first = _record(1)
    second = _record(2)
    (tmp_path / "002.jpg").write_bytes(b"jpg")
    second.assets.page_screenshot = tmp_path / "002.jpg"
    session = _session(tmp_path, [first, second])

    assert session.remove_record(1) is True

    assert session.evidence_ids() == [2]
    assert (tmp_path / "002.jpg").exists()


def test_remove_record_refuses_assets_outside_job_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    record = _record(1)
    record.assets.page_screenshot = outside
    session = _session(job_dir, [record])

    assert session.remove_record(1) is True

    assert outside.exists()  # 任务目录之外的文件绝不动
    assert session.evidence_ids() == []


def test_remove_record_unknown_id_returns_false(tmp_path: Path) -> None:
    session = _session(tmp_path, [_record(1)])
    assert session.remove_record(99) is False
    assert session.evidence_ids() == [1]
