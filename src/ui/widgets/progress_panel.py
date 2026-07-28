"""Animated progress, counts and current URL display."""

from __future__ import annotations

from PyQt5.QtCore import QTimer, Qt
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
        self._is_running = False
        self._pulse_step = 0

        # ── 阶段与计数 ──
        self.stage_label = QLabel("等待开始")
        self.stage_label.setObjectName("progressStage")
        self.stage_label.setStyleSheet(
            "font-weight: 700; font-size: 16px; color: #1a5a4a;"
        )
        self.count_label = QLabel("0 / 0")
        self.count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count_label.setProperty("muted", True)
        self.count_label.setStyleSheet("font-size: 15px;")
        heading = QHBoxLayout()
        heading.addWidget(self.stage_label, 1)
        heading.addWidget(self.count_label)

        # ── 进度条 ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("jobProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("")
        self.progress_bar.setMinimumHeight(18)
        self.progress_bar.setMaximumHeight(18)

        # ── 脉冲动画定时器 ──
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(600)
        self._pulse_timer.timeout.connect(self._pulse)

        # ── 统计卡片 ──
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_values: dict[str, QLabel] = {}
        for key, label, color in (
            ("ready",    "可导出",   "#1a7a64"),
            ("review",   "待补录",   "#b87d2a"),
            ("failed",   "失败",     "#b54a3e"),
            ("cancelled","已取消",   "#6b7a8d"),
        ):
            box, value_label = _stat_box(label, color)
            self.stat_values[key] = value_label
            stats.addWidget(box)

        # ── 当前 URL ──
        self.url_label = QLabel("当前页面：尚未开始")
        self.url_label.setObjectName("currentUrl")
        self.url_label.setProperty("muted", True)
        self.url_label.setMinimumHeight(32)
        self.url_label.setMaximumHeight(36)
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # ── 状态提示 ──
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("statusHint")
        self.status_hint.setProperty("muted", True)
        self.status_hint.setStyleSheet("font-size: 13px; color: #6b7a8d; font-style: italic;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(heading)
        layout.addWidget(self.progress_bar)
        layout.addLayout(stats)
        layout.addWidget(self.url_label)
        layout.addWidget(self.status_hint)

    def reset(self) -> None:
        self._stop_pulse()
        self.set_snapshot(ProgressSnapshot(0, 0, 0, 0, 0, 0))
        self.status_hint.setText("")
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

        # 根据进度状态更新提示
        if snapshot.percent < 100 and snapshot.total > 0:
            self._is_running = True
            self._start_pulse()
            if snapshot.percent == 0:
                self.status_hint.setText("正在初始化…")
            else:
                remaining = snapshot.total - snapshot.completed
                self.status_hint.setText(
                    f"正在处理中，还剩 {remaining} 条… "
                    f"已完成 {snapshot.ready} 条，"
                    f"待补录 {snapshot.needs_review} 条"
                    if snapshot.needs_review else
                    f"正在处理中，还剩 {remaining} 条…"
                )
        else:
            self._stop_pulse()
            if snapshot.failed > 0 or snapshot.needs_review > 0:
                self.status_hint.setText("部分完成，请查看下方结果详情。")
            elif snapshot.ready > 0:
                self.status_hint.setText("全部完成！可点击「打开输出位置」查看生成的压缩包。")
            else:
                self.status_hint.setText("")

    def _start_pulse(self) -> None:
        if not self._pulse_timer.isActive():
            self._pulse_step = 0
            self._pulse_timer.start()

    def _stop_pulse(self) -> None:
        self._pulse_timer.stop()
        # 恢复进度条正常样式
        self.progress_bar.setStyleSheet("")

    def _pulse(self) -> None:
        """让进度条在 0% 时有个脉冲动画，提示用户系统在运行。"""
        val = self.progress_bar.value()
        if val == 0:
            self._pulse_step = (self._pulse_step + 1) % 100
            # 用 QSS 模拟不确定进度条的脉冲
            pos = (self._pulse_step * 3) % 200 - 100
            self.progress_bar.setStyleSheet(
                f"""
                QProgressBar::chunk {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 transparent,
                        stop:{max(0, pos + 10)/200:.2f} #28a68a,
                        stop:{min(200, pos + 40)/200:.2f} #3dc4a8,
                        stop:1 transparent
                    );
                    border-radius: 6px;
                }}
                """
            )

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
            self._current_url,
            Qt.ElideMiddle,
            available,
        )
        self.url_label.setText(f"当前页面：{elided}")
        self.url_label.setToolTip(self._current_url)


def _stat_box(label: str, color: str) -> tuple[QFrame, QLabel]:
    box = QFrame()
    box.setObjectName("statBox")
    box.setMinimumHeight(56)
    value = QLabel("0")
    value.setObjectName("statValue")
    value.setStyleSheet(f"color: {color};")
    value.setAlignment(Qt.AlignCenter)
    caption = QLabel(label)
    caption.setProperty("muted", True)
    caption.setAlignment(Qt.AlignCenter)
    caption.setStyleSheet("font-size: 12px;")
    layout = QGridLayout(box)
    layout.setContentsMargins(14, 8, 14, 8)
    layout.setVerticalSpacing(2)
    layout.addWidget(value, 0, 0)
    layout.addWidget(caption, 1, 0)
    return box, value
