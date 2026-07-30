"""Round-trip tests for importing a delivered template.zip into a review job."""
from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from src.domain.template_schema import SHEET_LAYOUTS, SHEET_ORDER
from src.services.checkpoint_store import CheckpointStore
from src.services.review_session import ReviewSession
from src.services.zip_import import TemplateZipImporter, TemplateZipImportError


def _build_template_zip(target: Path) -> Path:
    """Create a minimal but contract-shaped template.zip fixture."""

    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name in SHEET_ORDER:
        layout = SHEET_LAYOUTS[name]
        sheet = workbook.create_sheet(name)
        sheet.append(list(layout.headers))
        sheet.append(["示例"] * layout.column_count)
    weibo = workbook["微博博客"]
    weibo.append([
        "https://example.test/post/1",
        "昵称甲",
        "新浪_新浪微博_博客贴吧",
        "正文",
        "2026-07-01 12:00:00",
        "完整内容",
        "shot_001.png",
        "",
    ])
    weibo.append([
        "https://example.test/post/2",
        "",
        "新浪_新浪微博_博客贴吧",
        "正文",
        "",
        "",
        "",
        "",
    ])
    qun = workbook["群聊"]
    qun.append(["", "", "微信-群聊", "用户A", "", "群聊内容", "", "chat.png", ""])

    staging = target / "build"
    (staging / "template").mkdir(parents=True)
    workbook.save(staging / "template" / "template.xlsx")
    (staging / "template" / "shot_001.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (staging / "template" / "chat.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    zip_path = target / "template.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in (staging / "template").iterdir():
            archive.write(path, f"template/{path.name}")
    return zip_path


def test_import_reconstructs_records_checkpoint_and_assets(tmp_path: Path) -> None:
    zip_path = _build_template_zip(tmp_path)
    importer = TemplateZipImporter(tmp_path / "output")
    job_dir = importer.import_zip(zip_path)

    assert job_dir.is_dir()
    assert (job_dir / "staging" / "template" / "template.xlsx").is_file()
    snapshot = CheckpointStore.load(job_dir / "job_checkpoint.json")
    assert len(snapshot.records) == 3

    first, second, third = snapshot.records
    # 证据编号按模板表序全局编号：群聊在微博博客之前
    assert first.task.evidence_id == 1
    assert first.route.sheet_name == "群聊"
    assert first.task.original_url == ""
    assert first.page.author_name == "用户A"
    assert first.assets.page_screenshot is not None

    assert second.task.original_url == "https://example.test/post/1"
    assert second.route.sheet_name == "微博博客"
    assert second.page.author_name == "昵称甲"
    assert second.page.published_at is not None
    assert second.assets.page_screenshot is not None
    assert second.assets.page_screenshot.name == "shot_001.png"
    # 完整行 → 已导出；缺失行 → 待补录
    assert second.status.value == "exported"
    assert third.status.value == "needs_review"

    # 导入后可直接进入补录会话并保存人工修改
    session = ReviewSession.from_job_dir(job_dir)
    session.set_field(3, "author_name", "补录昵称")
    session.set_field(3, "platform", "新浪_新浪微博_博客贴吧")
    reloaded = ReviewSession.from_job_dir(job_dir)
    views = {v.field: v for v in reloaded.field_views(3)}
    assert views["author_name"].value == "补录昵称"
    assert views["platform"].value == "新浪_新浪微博_博客贴吧"


def test_import_rejects_non_zip_and_missing_workbook(tmp_path: Path) -> None:
    importer = TemplateZipImporter(tmp_path / "output")
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip", encoding="utf-8")
    with pytest.raises(TemplateZipImportError):
        importer.import_zip(bad)

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(TemplateZipImportError):
        importer.import_zip(empty)
