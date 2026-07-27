"""Compact task settings with safe defaults and optional login state."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStyle,
    QWidget,
)

from src.config.settings import TaskConfig


class TaskOptionsWidget(QWidget):
    def __init__(self, defaults: TaskConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(118)
        self._defaults = defaults
        self.concurrency = _spin(1, 10, defaults.max_concurrency, " 个")
        self.timeout = _spin(5, 180, defaults.page_timeout_seconds, " 秒")
        self.retries = _spin(0, 5, defaults.max_retries, " 次")
        self.image_count = _spin(0, 50, defaults.max_images_per_record, " 张")
        image_mb = max(1, round(defaults.max_image_bytes / 1024 / 1024))
        self.image_size = _spin(1, 50, image_mb, " MB")
        self.screenshot_format = QComboBox()
        self.screenshot_format.addItem("JPG（体积更小）", "jpeg")
        self.screenshot_format.addItem("PNG（文字更清晰）", "png")
        index = self.screenshot_format.findData(defaults.screenshot_format)
        self.screenshot_format.setCurrentIndex(max(0, index))
        self.headless = QCheckBox("后台运行浏览器")
        self.headless.setChecked(defaults.headless)
        self.headless.setToolTip("取消勾选后会显示浏览器窗口，便于完成合法登录。")

        self.storage_state = QLineEdit()
        self.storage_state.setObjectName("storageStatePath")
        self.storage_state.setReadOnly(True)
        self.storage_state.setPlaceholderText("可留空：普通公开页面无需选择")
        if defaults.storage_state_path:
            self.storage_state.setText(str(defaults.storage_state_path))
        self.storage_button = QPushButton("选择登录态")
        self.storage_button.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.storage_button.clicked.connect(self._choose_storage_state)
        self.clear_storage_button = QPushButton()
        self.clear_storage_button.setToolTip("清除登录态文件")
        self.clear_storage_button.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        self.clear_storage_button.setFixedWidth(36)
        self.clear_storage_button.clicked.connect(self.storage_state.clear)

        storage_row = QHBoxLayout()
        storage_row.setContentsMargins(0, 0, 0, 0)
        storage_row.setSpacing(6)
        storage_row.addWidget(self.storage_state, 1)
        storage_row.addWidget(self.storage_button)
        storage_row.addWidget(self.clear_storage_button)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)
        _add_option(layout, 0, 0, "同时处理", self.concurrency)
        _add_option(layout, 0, 2, "单页超时", self.timeout)
        _add_option(layout, 0, 4, "失败重试", self.retries)
        _add_option(layout, 1, 0, "每页图片", self.image_count)
        _add_option(layout, 1, 2, "单图上限", self.image_size)
        _add_option(layout, 1, 4, "截图格式", self.screenshot_format)
        layout.addWidget(self.headless, 2, 0, 1, 2)
        layout.addWidget(QLabel("登录态 JSON（可选）"), 2, 2)
        layout.addLayout(storage_row, 2, 3, 1, 3)
        for column in (1, 3, 5):
            layout.setColumnStretch(column, 1)

    def task_config(self) -> TaskConfig:
        storage_text = self.storage_state.text().strip()
        storage_path = Path(storage_text) if storage_text else None
        if storage_path is not None and not storage_path.is_file():
            raise ValueError("选择的登录态 JSON 文件不存在。")
        return TaskConfig(
            max_concurrency=self.concurrency.value(),
            page_timeout_seconds=self.timeout.value(),
            max_retries=self.retries.value(),
            retry_base_delay_seconds=self._defaults.retry_base_delay_seconds,
            min_host_interval_seconds=self._defaults.min_host_interval_seconds,
            page_stabilize_milliseconds=self._defaults.page_stabilize_milliseconds,
            screenshot_format=str(self.screenshot_format.currentData()),
            full_page_screenshot=self._defaults.full_page_screenshot,
            max_images_per_record=self.image_count.value(),
            max_image_bytes=self.image_size.value() * 1024 * 1024,
            summary_max_chars=self._defaults.summary_max_chars,
            timezone=self._defaults.timezone,
            headless=self.headless.isChecked(),
            storage_state_path=storage_path,
            allow_nickname_as_id=self._defaults.allow_nickname_as_id,
        )

    def set_controls_enabled(self, enabled: bool) -> None:
        controls = (
            self.concurrency,
            self.timeout,
            self.retries,
            self.image_count,
            self.image_size,
            self.screenshot_format,
            self.headless,
            self.storage_button,
            self.clear_storage_button,
        )
        for control in controls:
            control.setEnabled(enabled)

    def _choose_storage_state(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Playwright 登录态 JSON",
            str(Path.home()),
            "JSON 文件 (*.json)",
        )
        if selected:
            self.storage_state.setText(str(Path(selected).resolve()))
            self.storage_state.setToolTip(selected)


def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
    control = QSpinBox()
    control.setRange(minimum, maximum)
    control.setValue(value)
    control.setSuffix(suffix)
    return control


def _add_option(
    layout: QGridLayout,
    row: int,
    column: int,
    label: str,
    control: QWidget,
) -> None:
    layout.addWidget(QLabel(label), row, column)
    layout.addWidget(control, row, column + 1)
