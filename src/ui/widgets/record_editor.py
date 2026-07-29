"""Per-record manual-entry editor used by the review workspace.

Shows every editable template field of the selected record with its crawled
value pre-filled, marks manual vs crawled sources, highlights missing
required fields, auto-saves edits (debounced) into the job's override store,
and hosts the fullscreen-capture controls.  URLs are rendered as links that
open directly in the system browser — no extra button needed.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.services.review_session import ReviewFieldView, ReviewSession

_PLACEHOLDER_EMPTY = "（未取到，可人工填写）"
_TIME_HINT = "yyyy-MM-dd HH:mm:ss"
_MISSING_STYLE = "border: 1px solid #d98880; background: #fdf0ee;"
_NORMAL_STYLE = ""


class RecordEditor(QWidget):
    """Editor for one record's overrideable template fields."""

    changed = pyqtSignal(int)
    screenshot_requested = pyqtSignal(str)  # "primary" | "attachment"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: ReviewSession | None = None
        self._evidence_id: int | None = None
        self._field_widgets: dict[str, QWidget] = {}
        self._field_badges: dict[str, QLabel] = {}
        self._field_labels: dict[str, QLabel] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_dirty_fields)
        self._dirty: set[str] = set()
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self._scroll)

        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(8, 8, 8, 16)
        self._layout.setSpacing(8)
        self._scroll.setWidget(self._body)

        self._placeholder = QLabel(
            "完成抓取后，或点击上方「打开任务目录」，即可在这里补录。"
        )
        self._placeholder.setProperty("muted", True)
        self._placeholder.setWordWrap(True)
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch(1)

    # ── public API ──
    def current_evidence_id(self) -> int | None:
        return self._evidence_id

    def load_record(self, session: ReviewSession, evidence_id: int) -> None:
        self._save_timer.stop()
        self._dirty.clear()
        self._session = session
        self._evidence_id = evidence_id
        self._loading = True
        try:
            self._rebuild()
        finally:
            self._loading = False

    def refresh_after_capture(self) -> None:
        if self._session is None or self._evidence_id is None:
            return
        self._loading = True
        try:
            self._refresh_screenshot_block()
            self._update_field_states()
        finally:
            self._loading = False

    def flush(self) -> None:
        """Persist any pending debounced edits immediately."""

        self._save_timer.stop()
        if self._dirty:
            self._save_dirty_fields()

    # ── build ──
    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._field_widgets.clear()
        self._field_badges.clear()
        self._field_labels.clear()
        assert self._session is not None and self._evidence_id is not None
        session = self._session
        record = session.get_record(self._evidence_id)
        summary = session.summary_for(record)

        header = QLabel(
            f"证据 {self._evidence_id:03d} · "
            f"{summary.sheet_name or '未匹配工作表'} "
            f"{summary.platform_value}"
        )
        header.setObjectName("editorHeader")
        self._layout.addWidget(header)

        if record.route is None:
            warning = QLabel("该 URL 未匹配固定模板平台，补录内容不会写入 template.zip。")
            warning.setStyleSheet("color: #b26a00;")
            warning.setWordWrap(True)
            self._layout.addWidget(warning)

        self._layout.addWidget(self._link_row("原始 URL", summary.original_url))
        if summary.final_url and summary.final_url != summary.original_url:
            self._layout.addWidget(self._link_row("最终 URL", summary.final_url))

        views = session.field_views(self._evidence_id)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for row, view in enumerate(views):
            label = QLabel(self._label_text(view))
            if view.missing:
                label.setStyleSheet("color: #c0392b;")
            badge = QLabel(view.source_text)
            badge.setProperty("muted", True)
            editor = self._build_field_editor(view)
            if view.missing and not view.choices:
                editor.setStyleSheet(_MISSING_STYLE)
            self._field_labels[view.field] = label
            self._field_badges[view.field] = badge
            self._field_widgets[view.field] = editor
            grid.addWidget(label, row, 0, Qt.AlignTop)
            grid.addWidget(editor, row, 1)
            grid.addWidget(badge, row, 2, Qt.AlignTop)
        self._layout.addLayout(grid)

        self._screenshot_host = QVBoxLayout()
        self._layout.addLayout(self._screenshot_host)
        self._refresh_screenshot_block()

        override = session.get_override(self._evidence_id)
        note_row = QHBoxLayout()
        note_row.addWidget(QLabel("备注"))
        self._note_edit = QLineEdit(override.note if override else "")
        self._note_edit.setPlaceholderText("仅保存在任务目录，不写入表格")
        self._note_edit.textChanged.connect(lambda _text: self._mark_dirty("note"))
        note_row.addWidget(self._note_edit, 1)
        self._layout.addLayout(note_row)

        self._save_state = QLabel("")
        self._save_state.setProperty("muted", True)
        self._layout.addWidget(self._save_state)
        self._layout.addStretch(1)

    def _build_field_editor(self, view: ReviewFieldView) -> QWidget:
        if view.choices:
            combo = QComboBox()
            combo.addItems(view.choices)
            combo.setCurrentText(view.value)
            combo.currentTextChanged.connect(
                lambda _text, field=view.field: self._mark_dirty(field)
            )
            return combo
        if view.multiline:
            area = QPlainTextEdit()
            area.setPlainText(view.value)
            area.setPlaceholderText(_PLACEHOLDER_EMPTY)
            area.setMinimumHeight(110)
            area.textChanged.connect(
                lambda field=view.field: self._mark_dirty(field)
            )
            return area
        edit = QLineEdit(view.value)
        edit.setPlaceholderText(
            _TIME_HINT if view.field == "published_at" else _PLACEHOLDER_EMPTY
        )
        edit.textChanged.connect(
            lambda _text, field=view.field: self._mark_dirty(field)
        )
        return edit

    def _link_row(self, label: str, url: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        link = QLabel(f'<a href="{escape(url)}">{escape(url)}</a>')
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.linkActivated.connect(
            lambda target: QDesktopServices.openUrl(QUrl(target))
        )
        link.setToolTip("点击直接在浏览器中打开")
        layout.addWidget(link, 1)
        return row

    def _refresh_screenshot_block(self) -> None:
        while self._screenshot_host.count():
            item = self._screenshot_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    child = sub.takeAt(0)
                    child_widget = child.widget()
                    if child_widget is not None:
                        child_widget.deleteLater()
        assert self._session is not None and self._evidence_id is not None
        session = self._session
        record = session.get_record(self._evidence_id)

        title = QLabel("截图")
        title.setObjectName("editorSection")
        self._screenshot_host.addWidget(title)

        name = session.primary_screenshot_name(record)
        override = session.get_override(self._evidence_id)
        manual_name = override.primary_screenshot_name if override else None
        name_label = QLabel(
            f"主截图：{name}" if name else "主截图：（无，点击「截全屏」补齐）"
        )
        if not name:
            name_label.setStyleSheet("color: #c0392b;")
        self._screenshot_host.addWidget(name_label)

        preview_path = session.primary_screenshot_path(record)
        if preview_path is not None:
            thumb = QLabel()
            thumb.setPixmap(_scaled_pixmap(preview_path, 300))
            thumb.setToolTip(str(preview_path))
            self._screenshot_host.addWidget(thumb)

        buttons = QHBoxLayout()
        capture_primary = QPushButton("📷 截全屏作为主截图")
        capture_primary.setToolTip("隐藏本窗口后截取整个屏幕，自动填为主截图")
        capture_primary.clicked.connect(
            lambda: self.screenshot_requested.emit("primary")
        )
        buttons.addWidget(capture_primary)
        capture_extra = QPushButton("📷 截全屏加入附件")
        capture_extra.clicked.connect(
            lambda: self.screenshot_requested.emit("attachment")
        )
        buttons.addWidget(capture_extra)
        if manual_name:
            remove = QPushButton("移除人工截图")
            remove.clicked.connect(self._remove_manual_screenshot)
            buttons.addWidget(remove)
        buttons.addStretch(1)
        self._screenshot_host.addLayout(buttons)

        attachments = list(override.attachment_names) if override else []
        if record.assets.author_screenshot is not None:
            attachments.insert(0, f"{record.assets.author_screenshot.name}（作者主页）")
        extra_label = QLabel(
            "其他附件：" + ("，".join(attachments) if attachments else "（无）")
        )
        extra_label.setWordWrap(True)
        self._screenshot_host.addWidget(extra_label)

    # ── saving ──
    def _mark_dirty(self, field: str) -> None:
        if self._loading or self._session is None:
            return
        self._dirty.add(field)
        self._save_timer.start()

    def _save_dirty_fields(self) -> None:
        if self._session is None or self._evidence_id is None:
            return
        session = self._session
        evidence_id = self._evidence_id
        for field in sorted(self._dirty):
            if field == "note":
                session.set_note(evidence_id, self._note_edit.text())
                continue
            widget = self._field_widgets.get(field)
            if widget is None:
                continue
            session.set_field(evidence_id, field, _widget_value(widget))
        self._dirty.clear()
        self._save_state.setText(
            f"已自动保存 {datetime.now().strftime('%H:%M:%S')}"
        )
        self._update_field_states()
        self.changed.emit(evidence_id)

    def _update_field_states(self) -> None:
        assert self._session is not None and self._evidence_id is not None
        for view in self._session.field_views(self._evidence_id):
            label = self._field_labels.get(view.field)
            badge = self._field_badges.get(view.field)
            widget = self._field_widgets.get(view.field)
            if label is not None:
                label.setText(self._label_text(view))
                label.setStyleSheet("color: #c0392b;" if view.missing else "")
            if badge is not None:
                badge.setText(view.source_text)
            if widget is not None and not view.choices:
                widget.setStyleSheet(_MISSING_STYLE if view.missing else _NORMAL_STYLE)

    @staticmethod
    def _label_text(view: ReviewFieldView) -> str:
        marker = " *" if view.required else ""
        return f"{view.label}{marker}"

    def _remove_manual_screenshot(self) -> None:
        if self._session is None or self._evidence_id is None:
            return
        self._session.set_primary_screenshot(self._evidence_id, None)
        self._refresh_screenshot_block()
        self.changed.emit(self._evidence_id)


def _widget_value(widget: QWidget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, QPlainTextEdit):
        return widget.toPlainText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return ""


def _scaled_pixmap(path: Path, width: int) -> QPixmap:
    pixmap = QPixmap(str(path))
    if pixmap.isNull() or pixmap.width() <= width:
        return pixmap
    return pixmap.scaledToWidth(width, Qt.SmoothTransformation)
