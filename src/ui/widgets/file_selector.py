"""Drag-and-drop URL input file selector."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)


SUPPORTED_SUFFIXES = {".txt", ".csv", ".xlsx"}


class FileSelector(QWidget):
    path_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("fileSelector")
        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("inputPath")
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("尚未选择文件，可点击右侧按钮或直接拖入")
        self.browse_button = QPushButton("选择文件")
        self.browse_button.setObjectName("browseInputButton")
        self.browse_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.browse_button.clicked.connect(self._browse)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_button)
        hint = QLabel("支持 TXT、CSV 和普通 XLSX。文件里只要出现网页链接即可，重复链接会自动去重。")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(row)
        layout.addWidget(hint)

    def path(self) -> Path | None:
        value = self.path_edit.text().strip()
        return Path(value) if value else None

    def set_path(self, path: Path | str) -> bool:
        candidate = Path(path)
        if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
            return False
        resolved = str(candidate.resolve())
        self.path_edit.setText(resolved)
        self.path_edit.setToolTip(resolved)
        self.path_changed.emit(resolved)
        return True

    def set_controls_enabled(self, enabled: bool) -> None:
        self.browse_button.setEnabled(enabled)
        self.setAcceptDrops(enabled)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and Path(urls[0].toLocalFile()).suffix.lower() in SUPPORTED_SUFFIXES:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls and self.set_path(urls[0].toLocalFile()):
            event.acceptProposedAction()
        else:
            QMessageBox.warning(self, "文件不支持", "请选择 TXT、CSV 或普通 XLSX 文件。")

    def _browse(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择包含网页链接的文件",
            str(Path.home()),
            "URL 文件 (*.txt *.csv *.xlsx)",
        )
        if selected:
            self.set_path(selected)
