"""Bounded local runtime log viewer."""

from __future__ import annotations

from datetime import datetime

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


class LogViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("运行日志只保存在本机，不会写入 template.zip")
        title.setProperty("muted", True)
        clear_button = QToolButton()
        clear_button.setObjectName("clearLogButton")
        clear_button.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        clear_button.setToolTip("清空当前日志")
        clear_button.clicked.connect(self.clear)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(title, 1)
        heading.addWidget(clear_button)
        self.text = QPlainTextEdit()
        self.text.setObjectName("logText")
        self.text.setReadOnly(True)
        self.text.document().setMaximumBlockCount(2_000)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(heading)
        layout.addWidget(self.text, 1)

    def append_event(self, event: LogEvent) -> None:
        labels = {
            "INFO": "信息",
            "WARNING": "提醒",
            "ERROR": "错误",
            "SUCCESS": "完成",
        }
        timestamp = event.timestamp.strftime("%H:%M:%S")
        label = labels.get(event.level.upper(), event.level)
        self.text.appendPlainText(f"[{timestamp}] [{label}] {event.message}")
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_message(self, level: str, message: str) -> None:
        self.append_event(LogEvent(datetime.now(DEFAULT_TIMEZONE), level, message))

    def clear(self) -> None:
        self.text.clear()
