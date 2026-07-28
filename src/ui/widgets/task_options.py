"""Spacious task settings with help descriptions and safe defaults."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
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
        "旧版综合登录态（兼容）",
        "仅用于兼容已有 Playwright storage state JSON。\n"
        "新登录态请使用“管理平台登录态”，按平台加密保存并单独复验。\n"
        "登录态仅保存在本机，不会写入 template.zip。",
    ),
}


class TaskOptionsWidget(QWidget):
    def __init__(self, defaults: TaskConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._defaults = defaults
        self.concurrency = _spin(1, 10, defaults.max_concurrency, " 个")
        self.timeout = _spin(5, 180, defaults.page_timeout_seconds, " 秒")
        self.retries = _spin(0, 5, defaults.max_retries, " 次")
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
        self.storage_state.setPlaceholderText("未启用登录态保存")
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

        # ── 用 QFormLayout 组织，简洁清晰 ──
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        def add(label: str, widget: QWidget, help_key: str) -> None:
            lbl = QLabel(label)
            lbl.setToolTip(_PARAM_HELP[help_key][1])
            widget.setToolTip(_PARAM_HELP[help_key][1])
            form.addRow(lbl, widget)

        add("同时处理", self.concurrency, "concurrency")
        add("单页超时", self.timeout, "timeout")
        add("失败重试", self.retries, "retries")
        add("截图格式", self.screenshot_format, "screenshot_format")
        layout.addLayout(form)

        asset_policy = QLabel("截图规则：每条最多 2 张（内容页 + 作者主页），不输出正文图片附件")
        asset_policy.setObjectName("assetPolicy")
        asset_policy.setWordWrap(True)
        layout.addWidget(asset_policy)

        self.headless.setToolTip(_PARAM_HELP["headless"][1])
        layout.addWidget(self.headless)

        storage_label = QLabel("旧版综合登录态（兼容）")
        storage_label.setToolTip(_PARAM_HELP["storage_state"][1])
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(storage_label)
        row.addWidget(self.storage_state, 1)
        row.addWidget(self.storage_button)
        row.addWidget(self.clear_storage_button)
        layout.addLayout(row)

    def task_config(self) -> TaskConfig:
        storage_text = self.storage_state.text().strip()
        storage_path = Path(storage_text) if storage_text else None
        if storage_path is not None and storage_path.suffix.casefold() != ".json":
            raise ValueError("登录态文件必须使用 .json 扩展名。")
        return TaskConfig(
            max_concurrency=self.concurrency.value(),
            page_timeout_seconds=self.timeout.value(),
            page_processing_timeout_seconds=self._defaults.page_processing_timeout_seconds,
            max_retries=self.retries.value(),
            retry_base_delay_seconds=self._defaults.retry_base_delay_seconds,
            min_host_interval_seconds=self._defaults.min_host_interval_seconds,
            page_stabilize_milliseconds=self._defaults.page_stabilize_milliseconds,
            screenshot_format=str(self.screenshot_format.currentData()),
            full_page_screenshot=self._defaults.full_page_screenshot,
            max_full_page_screenshot_height=self._defaults.max_full_page_screenshot_height,
            screenshot_jpeg_quality=self._defaults.screenshot_jpeg_quality,
            long_page_jpeg_quality=self._defaults.long_page_jpeg_quality,
            # Page images are bounded, temporary OCR inputs only. They are
            # deleted after recognition and never included in template.zip.
            max_images_per_record=self._defaults.max_images_per_record,
            max_image_bytes=self._defaults.max_image_bytes,
            summary_max_chars=self._defaults.summary_max_chars,
            export_content_max_chars=self._defaults.export_content_max_chars,
            timezone=self._defaults.timezone,
            headless=self.headless.isChecked(),
            storage_state_path=storage_path,
            auth_store_dir=self._defaults.auth_store_dir,
            manual_intervention_timeout_seconds=self._defaults.manual_intervention_timeout_seconds,
            allow_nickname_as_id=self._defaults.allow_nickname_as_id,
            ocr_enabled=self._defaults.ocr_enabled,
            ocr_confidence_threshold=self._defaults.ocr_confidence_threshold,
            ocr_python_executable=self._defaults.ocr_python_executable,
            ocr_worker_timeout_seconds=self._defaults.ocr_worker_timeout_seconds,
            ocr_max_restarts=self._defaults.ocr_max_restarts,
            ocr_min_image_width=self._defaults.ocr_min_image_width,
            ocr_min_image_height=self._defaults.ocr_min_image_height,
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
