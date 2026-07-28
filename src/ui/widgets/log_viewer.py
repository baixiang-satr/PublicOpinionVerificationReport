"""Bounded local runtime log viewer with color-coded levels."""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtGui import QColor, QTextCharFormat
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.services.models import LogEvent
from src.utils.time_utils import DEFAULT_TIMEZONE


_LEVEL_LABEL = {
    "INFO": "[I]",
    "WARNING": "[W]",
    "ERROR": "[E]",
    "SUCCESS": "[O]",
}
_LEVEL_COLOR = {
    "INFO": QColor("#7ec6e0"),
    "WARNING": QColor("#e8c84a"),
    "ERROR": QColor("#e86a5a"),
    "SUCCESS": QColor("#5fc87a"),
}


class LogViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("日志仅保存在本机，不会写入压缩包")
        title.setProperty("muted", True)
        clear_button = QToolButton()
        clear_button.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        clear_button.setToolTip("清空")
        clear_button.clicked.connect(self.clear)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(title, 1)
        heading.addWidget(clear_button)

        self.text = QPlainTextEdit()
        self.text.setObjectName("logText")
        self.text.setReadOnly(True)
        self.text.document().setMaximumBlockCount(2_000)

        # 基础格式
        self._base_fmt = QTextCharFormat()
        self._base_fmt.setForeground(QColor("#dce4e9"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addLayout(heading)
        layout.addWidget(self.text, 1)

    def append_event(self, event: LogEvent) -> None:
        ts = event.timestamp.strftime("%H:%M:%S")
        tag = _LEVEL_LABEL.get(event.level.upper(), "[?]")
        color = _LEVEL_COLOR.get(event.level.upper(), QColor("#7a8898"))
        line = f"{ts} {tag} {event.message}"

        cursor = self.text.textCursor()
        cursor.movePosition(cursor.End)
        self.text.setTextCursor(cursor)

        fmt = QTextCharFormat(self._base_fmt)
        fmt.setForeground(color)
        cursor.insertText(line + "\n", fmt)

        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_message(self, level: str, message: str) -> None:
        self.append_event(LogEvent(datetime.now(DEFAULT_TIMEZONE), level, message))

    def clear(self) -> None:
        self.text.clear()
