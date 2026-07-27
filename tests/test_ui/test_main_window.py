from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.domain.models import PageData, RecordResult, RecordStatus, RouteDecision, UrlTask
from src.services.models import ProgressSnapshot
from src.ui.app import create_application
from src.ui.main_window import MainWindow


pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def window(app: QApplication, tmp_path: Path):
    source = tmp_path / "template"
    source.mkdir()
    (source / "template.xlsx").write_bytes(b"template")
    config = AppConfig(
        template=TemplateConfig(source_dir=source, output_dir=tmp_path / "output"),
        task=TaskConfig(),
    )
    main_window = MainWindow(config)
    main_window.resize(1100, 800)
    main_window.show()
    app.processEvents()
    yield main_window
    main_window.close()
    app.processEvents()


def test_main_window_exposes_complete_three_step_workflow(
    window: MainWindow,
    app: QApplication,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "urls.txt"
    input_path.write_text("https://example.test/article", encoding="utf-8")

    assert window.file_selector.set_path(input_path)
    assert window.file_selector.path() == input_path.resolve()
    assert window.start_button.text() == "开始生成"
    assert window.cancel_button.text() == "取消任务"
    assert window.retry_button.text() == "重试失败项"
    assert window.open_output_button.text() == "打开输出位置"
    assert window.result_table.columnCount() == 10
    assert not window.cancel_button.isEnabled()
    assert window.options.task_config().max_concurrency == 3

    pixmap = window.grab()
    image = pixmap.toImage()
    sampled_colors = {
        image.pixelColor(x, y).name()
        for x in range(0, image.width(), 80)
        for y in range(0, image.height(), 60)
    }
    assert image.width() == 1100 and image.height() == 800
    assert len(sampled_colors) >= 5
    app.processEvents()


def test_progress_and_audit_record_fit_without_changing_layout(
    window: MainWindow,
    app: QApplication,
) -> None:
    window.progress_panel.set_snapshot(
        ProgressSnapshot(
            completed=1,
            total=3,
            ready=1,
            needs_review=0,
            failed=0,
            cancelled=0,
            current_url="https://example.test/a/very/long/path/that/must/not/resize/the/window",
            stage="正在抓取网页",
        )
    )
    result = RecordResult(
        task=UrlTask(1, "https://example.test/a", "https://example.test/a"),
        status=RecordStatus.NEEDS_REVIEW,
        page=PageData(
            final_url="https://example.test/a",
            title="需要人工确认的测试标题",
            author_name="测试作者",
            author_url="https://example.test/author",
            status_code=200,
        ),
        route=RouteDecision("微博博客", "知乎_知乎_博客贴吧", "正文"),
    )
    window.result_table.set_record(result)
    app.processEvents()

    assert window.progress_panel.progress_bar.value() == 33
    assert window.result_table.rowCount() == 1
    assert window.result_table.item(0, 8).text() == "待人工补录"
    assert window.tabs.geometry().bottom() <= window.centralWidget().height()
