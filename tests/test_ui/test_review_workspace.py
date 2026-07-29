from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from src.domain.models import (
    AssetSet,
    PageData,
    RecordResult,
    RecordStatus,
    RouteDecision,
    UrlTask,
)
from src.services.checkpoint_store import CheckpointStore
from src.services.override_store import ManualOverrideStore
from src.ui.app import create_application
from src.ui.widgets.review_workspace import ReviewWorkspace


pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


def _record(evidence_id: int, **page_kwargs) -> RecordResult:
    task = UrlTask(
        evidence_id,
        f"https://example.test/p/{evidence_id}",
        f"https://example.test/p/{evidence_id}",
    )
    return RecordResult(
        task,
        RecordStatus.NEEDS_REVIEW,
        page=PageData(**page_kwargs),
        route=RouteDecision("微博博客", "新浪_新浪微博_博客贴吧", "正文"),
        assets=AssetSet(),
    )


def _save_checkpoint(job_dir: Path, records: list[RecordResult]) -> None:
    store = CheckpointStore(
        job_dir / "job_checkpoint.json",
        job_id="job-test",
        tasks=tuple(record.task for record in records),
    )
    store.update_many(records)


def test_workspace_loads_records_and_edits_persist(
    app: QApplication,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    records = [
        _record(1, author_name="抓取昵称"),
        _record(2, title="t", content_text="c", author_name="a"),
    ]
    workspace = ReviewWorkspace()
    workspace.load_records(job_dir, records)
    app.processEvents()

    # 默认只看待补录：两条都缺字段，列表两行。
    assert workspace.table.rowCount() == 2
    workspace.table.selectRow(0)
    app.processEvents()
    assert workspace.editor.current_evidence_id() == 1

    # 编辑昵称字段并强制落盘。
    author_widget = workspace.editor._field_widgets["author_name"]
    assert isinstance(author_widget, QLineEdit)
    author_widget.setText("人工昵称")
    workspace.editor.flush()
    app.processEvents()

    stored = ManualOverrideStore(job_dir).load().get(1)
    assert stored is not None
    assert stored.values["author_name"] == "人工昵称"

    # 从目录重新加载（模拟历史任务继续补录）。
    _save_checkpoint(job_dir, records)
    other = ReviewWorkspace()
    assert other.load_job(job_dir)
    app.processEvents()
    assert other.job_dir() == job_dir
    workspace.close()
    other.close()


def test_editor_shows_clickable_url_and_field_states(
    app: QApplication,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    records = [_record(1)]
    workspace = ReviewWorkspace()
    workspace.load_records(job_dir, records)
    app.processEvents()
    workspace.table.selectRow(0)
    app.processEvents()
    editor = workspace.editor

    # URL 以链接形式呈现（点击即跳转，不需要按钮）。
    html_texts = [
        label.text()
        for label in editor.findChildren(QLabel)
        if "<a href=" in label.text()
    ]
    assert any("https://example.test/p/1" in text for text in html_texts)

    # 必填缺失字段被标红样式；正文为多行编辑。
    content_widget = editor._field_widgets["content"]
    assert isinstance(content_widget, QPlainTextEdit)
    author_widget = editor._field_widgets["author_name"]
    assert "border" in author_widget.styleSheet()  # missing highlight
    workspace.close()
