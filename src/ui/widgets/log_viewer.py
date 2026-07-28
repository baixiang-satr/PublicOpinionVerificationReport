"""Bounded local runtime log viewer with color-coded levels."""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCharFormat, QColor, QTextCursor
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


# 日志级别颜色
_LEVEL_COLORS = {
    "INFO":    ("#8ab4d6", "ℹ"),
    "WARNING": ("#e8b84a", "⚠"),
    "ERROR":   ("#e86a5a", "✗"),
    "SUCCESS": ("#5fc87a", "✓"),
    "DEBUG":   ("#7a8898", "·"),
}


class LogViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("运行日志只保存在本机，不会写入 template.zip")
        title.setProperty("muted", True)
        title.setStyleSheet("font-size: 13px;")
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
        self.text.document().setMaximumBlockCount(5_000)
        # 暗色背景的默认格式
        self._default_fmt = QTextCharFormat()
        self._default_fmt.setForeground(QColor("#dce4e9"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(heading)
        layout.addWidget(self.text, 1)

    def append_event(self, event: LogEvent) -> None:
        level_upper = event.level.upper()
        color_hex, icon = _LEVEL_COLORS.get(level_upper, ("#7a8898", "·"))
        timestamp = event.timestamp.strftime("%H:%M:%S")

        # 使用 HTML 片段实现颜色
        html = (
            f'<span style="color:#5a6a7a;">[{timestamp}]</span> '
            f'<span style="color:{color_hex};font-weight:600;">[{icon} {event.level}]</span> '
            f'<span style="color:#dce4e9;">{_escape_html(event.message)}</span>'
        )
        self.text.appendHtml(html)
        # 自动滚动到底部
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_message(self, level: str, message: str) -> None:
        self.append_event(LogEvent(datetime.now(DEFAULT_TIMEZONE), level, message))

    def clear(self) -> None:
        self.text.clear()


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
