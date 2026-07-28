"""Spacious task settings with help descriptions and safe defaults."""

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
    QVBoxLayout,
    QWidget,
)

from src.config.settings import TaskConfig


# ── 参数说明表 ──
_PARAM_HELP: dict[str, tuple[str, str]] = {
    "concurrency": (
        "同时处理几个页面",
        "数值越大跑得越快，但会增加电脑负担和反爬风险。\n"
        "普通电脑建议 3~5，性能好的可以开到 8~10。",
    ),
    "timeout": (
        "单个页面最长等待时间",
        "超过此时间仍没有加载完成的页面会视为超时。\n"
        "网速慢或页面复杂时可适当增大。",
    ),
    "retries": (
        "失败后重试次数",
        "抓取出错后自动重试几次。0 表示出错就跳过。\n"
        "网络不稳定时建议设为 2~3 次。",
    ),
    "image_count": (
        "每页最多保存几张图片",
        "从当前页面截取几张关键截图作为证据。\n"
        "0 表示不保存图片，只记录文字信息。",
    ),
    "image_size": (
        "每张图片大小上限",
        "超过此大小的截图会被压缩，以节省空间和生成速度。\n"
        "一般 1~2 MB 足够清晰。",
    ),
    "screenshot_format": (
        "截图文件格式",
        "JPG：体积小（适合大多数情况）\n"
        "PNG：文字更清晰（图片较大）",
    ),
    "headless": (
        "后台运行浏览器（无界面模式）",
        "勾选后浏览器在后台静默运行，您不会看到浏览器窗口。\n"
        "取消勾选则会显示浏览器窗口，便于您手工登录或处理验证码。",
    ),
    "storage_state": (
        "登录态文件（可选）",
        "如果您要抓取的页面需要登录才能访问，\n"
        "可以先用 Playwright 导出登录态 JSON 文件，然后在这里选择。\n"
        "普通公开页面无需此设置。",
    ),
}


class TaskOptionsWidget(QWidget):
    def __init__(self, defaults: TaskConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
        self.headless = QCheckBox("后台运行浏览器（无界面模式）")
        self.headless.setChecked(defaults.headless)
        self.headless.setToolTip(
            "勾选：浏览器在后台静默运行，不会弹出窗口\n"
            "取消勾选：弹出浏览器窗口，方便您手工登录"
        )

        self.storage_state = QLineEdit()
        self.storage_state.setObjectName("storageStatePath")
        self.storage_state.setReadOnly(True)
        self.storage_state.setPlaceholderText("未选择 — 普通公开页面无需登录态")
        if defaults.storage_state_path:
            self.storage_state.setText(str(defaults.storage_state_path))
        self.storage_button = QPushButton("浏览…")
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

        # ── 用垂直布局 + 网格行来组织，每行都带说明 ──
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 第 1 行：并发 / 超时 / 重试
        row1 = QGridLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setHorizontalSpacing(16)
        row1.setVerticalSpacing(4)
        _add_option_row(row1, 0, "同时处理", self.concurrency, _PARAM_HELP["concurrency"])
        _add_option_row(row1, 0, "单页超时", self.timeout, _PARAM_HELP["timeout"])
        _add_option_row(row1, 0, "失败重试", self.retries, _PARAM_HELP["retries"])
        layout.addLayout(row1)

        # 第 2 行：图片数量 / 图片大小 / 截图格式
        row2 = QGridLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setHorizontalSpacing(16)
        row2.setVerticalSpacing(4)
        _add_option_row(row2, 0, "每页图片", self.image_count, _PARAM_HELP["image_count"])
        _add_option_row(row2, 0, "单图上限", self.image_size, _PARAM_HELP["image_size"])
        _add_option_row(row2, 0, "截图格式", self.screenshot_format, _PARAM_HELP["screenshot_format"])
        layout.addLayout(row2)

        # 第 3 行：后台运行
        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(8)
        self.headless.setToolTip(_PARAM_HELP["headless"][1])
        row3.addWidget(self.headless)
        row3.addStretch()
        layout.addLayout(row3)

        # 第 4 行：登录态
        storage_label = QLabel("登录态 JSON（可选）")
        storage_label.setToolTip(_PARAM_HELP["storage_state"][1])
        row4 = QHBoxLayout()
        row4.setContentsMargins(0, 0, 0, 0)
        row4.setSpacing(8)
        row4.addWidget(storage_label)
        row4.addLayout(storage_row, 1)
        layout.addLayout(row4)

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
            manual_intervention_timeout_seconds=self._defaults.manual_intervention_timeout_seconds,
            allow_nickname_as_id=self._defaults.allow_nickname_as_id,
            ocr_enabled=self._defaults.ocr_enabled,
            ocr_confidence_threshold=self._defaults.ocr_confidence_threshold,
            # ── Anti-detection (use defaults, configurable via env) ──
            enable_stealth=self._defaults.enable_stealth,
            enable_extra_stealth=self._defaults.enable_extra_stealth,
            proxy_url=self._defaults.proxy_url,
            user_agent=self._defaults.user_agent,
            viewport_width=self._defaults.viewport_width,
            viewport_height=self._defaults.viewport_height,
            extra_chromium_args=self._defaults.extra_chromium_args,
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


def _add_option_row(
    layout: QGridLayout,
    row: int,
    label: str,
    control: QWidget,
    help_info: tuple[str, str],
) -> None:
    """添加一行：标签 | 控件，并设置 ToolTip。"""
    lbl = QLabel(label)
    title_text, help_text = help_info
    lbl.setToolTip(f"{title_text}\n\n{help_text}")
    control.setToolTip(help_text)
    layout.addWidget(lbl, row, 0)
    layout.addWidget(control, row, 1)
