"""Review workspace tab: browse crawl results, edit fields, capture screens.

Left side lists every record with its completeness; right side hosts the
:class:`RecordEditor`.  Edits are persisted as manual overrides inside the
job directory, so a historical job folder can be reopened at any time to
continue manual entry and re-export ``template.zip``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.domain.models import RecordResult, RecordStatus
from src.services.review_session import ReviewSession
from src.ui.tools.screen_capture import FullScreenCapturer
from src.ui.widgets.record_editor import RecordEditor

_STATUS_TEXT = {
    RecordStatus.PENDING: "等待中",
    RecordStatus.RUNNING: "处理中",
    RecordStatus.CRAWLED: "已抓取",
    RecordStatus.ROUTED: "已抓取",
    RecordStatus.ASSETS_READY: "已抓取",
    RecordStatus.READY_FOR_EXPORT: "可导出",
    RecordStatus.NEEDS_REVIEW: "待补录",
    RecordStatus.FAILED: "失败",
    RecordStatus.CANCELLED: "已取消",
    RecordStatus.EXPORTED: "已导出",
}

_ATTENTION_BG = QColor("#fdf3e4")
_OK_BG = QColor("#eaf5f0")


class ReviewWorkspace(QWidget):
    """「采集与补录」主界面。"""

    export_requested = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: ReviewSession | None = None
        self._row_by_id: dict[int, int] = {}
        self._id_by_row: dict[int, int] = {}
        self._capture_mode = "primary"
        self._capturer = FullScreenCapturer(self)
        self._capturer.captured.connect(self._on_captured)
        self._capturer.failed.connect(self._on_capture_failed)
        self._build_ui()

    # ── public API ──
    def load_job(self, job_dir: Path) -> bool:
        try:
            session = ReviewSession.from_job_dir(job_dir)
        except Exception as error:
            QMessageBox.warning(
                self,
                "无法打开任务目录",
                f"{job_dir}\n\n{error}",
            )
            return False
        self._set_session(session)
        return True

    def load_records(self, job_dir: Path, records: list[RecordResult]) -> None:
        self._set_session(ReviewSession.from_records(job_dir, records))

    def job_dir(self) -> Path | None:
        return self._session.job_dir if self._session is not None else None

    def has_session(self) -> bool:
        return self._session is not None

    # ── UI ──
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.open_button = QPushButton("打开任务目录…")
        self.open_button.setToolTip("选择 output 下的历史任务目录，继续人工补录。")
        self.open_button.clicked.connect(self._open_job_dir)
        toolbar.addWidget(self.open_button)

        self.export_button = QPushButton("导出 template.zip")
        self.export_button.setEnabled(False)
        self.export_button.setToolTip("合并人工补录内容，重新生成可上传的 template.zip。")
        self.export_button.clicked.connect(self._request_export)
        toolbar.addWidget(self.export_button)

        toolbar.addSpacing(16)
        self.only_attention = QCheckBox("只看待补录")
        self.only_attention.setChecked(True)
        self.only_attention.toggled.connect(self._refresh_list)
        toolbar.addWidget(self.only_attention)

        toolbar.addWidget(QLabel("平台"))
        self.platform_filter = QComboBox()
        self.platform_filter.currentIndexChanged.connect(self._refresh_list)
        toolbar.addWidget(self.platform_filter)

        toolbar.addWidget(QLabel("批量文本类型"))
        self.batch_text_type = QComboBox()
        self.batch_text_type.addItem("（选择后应用）")
        self.batch_text_type.addItems(["正文", "评论回复", "商家"])
        self.batch_text_type.activated.connect(self._apply_batch_text_type)
        toolbar.addWidget(self.batch_text_type)

        toolbar.addStretch(1)
        self.counts_label = QLabel("尚未加载任务")
        self.counts_label.setProperty("muted", True)
        toolbar.addWidget(self.counts_label)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("编号", "工作表/平台", "状态", "仍缺字段"))
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(2, 64)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        splitter.addWidget(self.table)

        self.editor = RecordEditor()
        self.editor.setMinimumHeight(90)
        self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.editor.changed.connect(self._on_record_changed)
        self.editor.screenshot_requested.connect(self._start_capture)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        # Keep the tab page's sizeHint flat so the fixed main-window layout
        # contract (tabs bottom must fit the viewport) stays intact.
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.prev_button = QPushButton("◀ 上一条待补录")
        self.prev_button.clicked.connect(lambda: self._jump_attention(backwards=True))
        bottom.addWidget(self.prev_button)
        self.next_button = QPushButton("下一条待补录 ▶")
        self.next_button.clicked.connect(lambda: self._jump_attention(backwards=False))
        bottom.addWidget(self.next_button)
        self.copy_button = QPushButton("复制上一条空字段")
        self.copy_button.setToolTip("把上一条记录的有效值复制到当前记录仍为空的字段（不覆盖已有内容）。")
        self.copy_button.clicked.connect(self._copy_from_previous)
        bottom.addWidget(self.copy_button)
        hint = QLabel("快捷键：Ctrl+↑ / Ctrl+↓ 跳转待补录；点击 URL 直接在浏览器打开原页面")
        hint.setProperty("muted", True)
        bottom.addWidget(hint, 1)
        layout.addLayout(bottom)

        QShortcut(QKeySequence("Ctrl+Down"), self, activated=lambda: self._jump_attention(backwards=False))
        QShortcut(QKeySequence("Ctrl+Up"), self, activated=lambda: self._jump_attention(backwards=True))

    def _set_session(self, session: ReviewSession) -> None:
        self._session = session
        self.export_button.setEnabled(True)
        completion = session.sheet_completion()
        self.platform_filter.blockSignals(True)
        self.platform_filter.clear()
        self.platform_filter.addItem("全部")
        for name in sorted(completion):
            done, total = completion[name]
            self.platform_filter.addItem(f"{name} ({done}/{total})", userData=name)
        self.platform_filter.blockSignals(False)
        self._refresh_list()
        self._select_row(0)

    # ── list ──
    def _refresh_list(self) -> None:
        if self._session is None:
            return
        current_id = self.editor.current_evidence_id()
        sheet_filter = (
            self.platform_filter.currentData()
            if self.platform_filter.currentIndex() > 0
            else None
        )
        only_attention = self.only_attention.isChecked()
        summaries = [
            summary
            for summary in self._session.summaries()
            if (not only_attention or summary.needs_attention)
            and (sheet_filter is None or (summary.sheet_name or "未匹配") == sheet_filter)
        ]
        self.table.setRowCount(0)
        self._row_by_id.clear()
        self._id_by_row.clear()
        for summary in summaries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_by_id[summary.evidence_id] = row
            self._id_by_row[row] = summary.evidence_id
            values = (
                f"{summary.evidence_id:03d}",
                summary.sheet_name or "未匹配",
                _STATUS_TEXT.get(summary.status, summary.status.value),
                "、".join(summary.missing_labels) or ("无" if not summary.needs_attention else "—"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if summary.needs_attention:
                    item.setBackground(_ATTENTION_BG)
                else:
                    item.setBackground(_OK_BG)
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        done, total = self._session.completion_counts()
        self.counts_label.setText(
            f"共 {total} 条 · 无需补录 {done} · 待补录 {total - done}"
            f"（当前显示 {len(summaries)} 条）"
        )
        if current_id in self._row_by_id:
            self.table.selectRow(self._row_by_id[current_id])

    def _select_row(self, row: int) -> None:
        if self.table.rowCount():
            self.table.selectRow(max(0, min(row, self.table.rowCount() - 1)))

    def _selected_evidence_ids(self) -> list[int]:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        return [
            self._id_by_row[row] for row in rows if row in self._id_by_row
        ]

    def _on_selection_changed(self) -> None:
        if self._session is None:
            return
        self.editor.flush()
        selected = self._selected_evidence_ids()
        if not selected:
            return
        self.editor.load_record(self._session, selected[0])

    def _on_record_changed(self, evidence_id: int) -> None:
        self._refresh_list()

    def _apply_batch_text_type(self) -> None:
        if self._session is None or self.batch_text_type.currentIndex() == 0:
            return
        text_type = self.batch_text_type.currentText()
        self.batch_text_type.setCurrentIndex(0)
        selected = self._selected_evidence_ids()
        if not selected:
            QMessageBox.information(self, "未选择记录", "请先在左侧列表中选择一条或多条记录。")
            return
        self.editor.flush()
        skipped = self._session.set_text_type_many(selected, text_type)
        self._refresh_list()
        if skipped:
            QMessageBox.information(
                self,
                "部分未应用",
                f"{len(skipped)} 条记录的工作表不允许文本类型「{text_type}」，已跳过。",
            )
        current = self.editor.current_evidence_id()
        if current is not None:
            self.editor.load_record(self._session, current)

    def _copy_from_previous(self) -> None:
        if self._session is None:
            return
        current = self.editor.current_evidence_id()
        if current is None:
            return
        previous = self._session.previous_id(current)
        if previous is None:
            QMessageBox.information(self, "没有上一条", "当前已是第一条记录。")
            return
        self.editor.flush()
        copied = self._session.copy_empty_fields_from(previous, current)
        self.editor.load_record(self._session, current)
        self._refresh_list()
        if copied:
            self.window().statusBar().showMessage(
                f"已从 {previous:03d} 复制 {len(copied)} 个字段", 5000
            )
        else:
            self.window().statusBar().showMessage("没有可复制的空字段", 5000)

    def _jump_attention(self, *, backwards: bool) -> None:
        if self._session is None:
            return
        current = self.editor.current_evidence_id()
        if current is None:
            ids = self._session.evidence_ids()
            current = ids[0] - 1 if ids and not backwards else (ids[-1] + 1 if ids else 0)
        target = self._session.next_attention_id(current, backwards=backwards)
        if target is None:
            QMessageBox.information(self, "全部完成", "没有待补录的记录了。")
            return
        if target not in self._row_by_id:
            self.only_attention.setChecked(False)
        row = self._row_by_id.get(target)
        if row is not None:
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))

    # ── screenshot ──
    def _start_capture(self, mode: str) -> None:
        if self._session is None or self.editor.current_evidence_id() is None:
            return
        self.editor.flush()
        self._capture_mode = mode
        self._capturer.start(self.window())

    def _on_captured(self, pixmap: QPixmap) -> None:
        if self._session is None:
            return
        evidence_id = self.editor.current_evidence_id()
        if evidence_id is None:
            return
        assets_dir = self._session.manual_assets_dir()
        assets_dir.mkdir(parents=True, exist_ok=True)
        name = f"{evidence_id:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        target = assets_dir / name
        if not pixmap.save(str(target), "PNG"):
            QMessageBox.warning(self, "截图失败", f"无法保存到 {target}")
            return
        record = self._session.get_record(evidence_id)
        if self._capture_mode == "primary":
            existing = self._session.primary_screenshot_name(record)
            override = self._session.get_override(evidence_id)
            if existing and not (override and override.primary_screenshot_name):
                answer = QMessageBox.question(
                    self,
                    "替换主截图？",
                    f"该记录已有抓取截图 {existing}，用人工截图替换吗？\n"
                    "（选 No 则保存为其他附件）",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if answer == QMessageBox.Cancel:
                    return
                if answer == QMessageBox.No:
                    self._append_attachment(evidence_id, name)
                    self.editor.refresh_after_capture()
                    self._refresh_list()
                    return
            self._session.set_primary_screenshot(evidence_id, name)
        else:
            self._append_attachment(evidence_id, name)
        self.editor.refresh_after_capture()
        self._refresh_list()

    def _append_attachment(self, evidence_id: int, name: str) -> None:
        override = self._session.get_override(evidence_id)
        names = list(override.attachment_names) if override else []
        if name not in names:
            names.append(name)
        self._session.set_attachments(evidence_id, names)

    def _on_capture_failed(self, message: str) -> None:
        QMessageBox.warning(self, "截图失败", message)

    # ── actions ──
    def _open_job_dir(self) -> None:
        base = self.job_dir() or Path("output")
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择任务输出目录（包含 job_checkpoint.json）",
            str(base),
        )
        if directory:
            self.load_job(Path(directory))

    def _request_export(self) -> None:
        if self._session is None:
            return
        self.editor.flush()
        done, total = self._session.completion_counts()
        if total - done > 0:
            answer = QMessageBox.question(
                self,
                "仍有待补录记录",
                f"还有 {total - done} 条记录缺少必填项，这些行将按空缺导出。\n继续导出吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.export_requested.emit(self._session.job_dir)
