"""Animated progress, counts and current URL display."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QResizeEvent
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from src.services.models import ProgressSnapshot


class ProgressPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_url = ""

        self.stage_label = QLabel("等待开始")
        self.stage_label.setStyleSheet("font-weight: 600;")
        self.count_label = QLabel("0 / 0")
        self.count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count_label.setProperty("muted", True)
        heading = QHBoxLayout()
        heading.addWidget(self.stage_label, 1)
        heading.addWidget(self.count_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("")

        stats = QHBoxLayout()
        stats.setSpacing(8)
        self.stat_values: dict[str, QLabel] = {}
        for key, label, color in (
            ("ready",    "可导出",   "#0b6e69"),
            ("review",   "待补录",   "#aa7526"),
            ("failed",   "失败",     "#a84035"),
            ("cancelled","已取消",   "#6d7f86"),
        ):
            box, value_label = _stat_box(label, color)
            self.stat_values[key] = value_label
            stats.addWidget(box)

        self.url_label = QLabel("当前页面：尚未开始")
        self.url_label.setObjectName("currentUrl")
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(heading)
        layout.addWidget(self.progress_bar)
        layout.addLayout(stats)
        layout.addWidget(self.url_label)

    def reset(self) -> None:
        self.set_snapshot(ProgressSnapshot(0, 0, 0, 0, 0, 0))
        self.progress_bar.setFormat("")
        self.stage_label.setText("等待开始")

    def set_snapshot(self, snapshot: ProgressSnapshot) -> None:
        self.stage_label.setText(snapshot.stage)
        self.count_label.setText(f"{snapshot.completed} / {snapshot.total}")
        self.progress_bar.setValue(snapshot.percent)
        if snapshot.total > 0:
            self.progress_bar.setFormat(f"{snapshot.percent}%")

        self.stat_values["ready"].setText(str(snapshot.ready))
        self.stat_values["review"].setText(str(snapshot.needs_review))
        self.stat_values["failed"].setText(str(snapshot.failed))
        self.stat_values["cancelled"].setText(str(snapshot.cancelled))

        self._current_url = snapshot.current_url
        self._render_url()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render_url()

    def _render_url(self) -> None:
        if not self._current_url:
            self.url_label.setText("当前页面：尚未开始")
            self.url_label.setToolTip("")
            return
        available = max(160, self.url_label.width() - 80)
        elided = self.url_label.fontMetrics().elidedText(
            self._current_url, Qt.ElideMiddle, available,
        )
        self.url_label.setText(f"当前页面：{elided}")
        self.url_label.setToolTip(self._current_url)


def _stat_box(label: str, color: str) -> tuple[QFrame, QLabel]:
    box = QFrame()
    box.setObjectName("statBox")
    box.setMinimumHeight(48)
    value = QLabel("0")
    value.setObjectName("statValue")
    value.setStyleSheet(f"color: {color};")
    value.setAlignment(Qt.AlignCenter)
    caption = QLabel(label)
    caption.setProperty("muted", True)
    caption.setAlignment(Qt.AlignCenter)
    layout = QGridLayout(box)
    layout.setContentsMargins(10, 6, 10, 6)
    layout.setVerticalSpacing(1)
    layout.addWidget(value, 0, 0)
    layout.addWidget(caption, 1, 0)
    return box, value
