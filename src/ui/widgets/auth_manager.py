"""Platform authentication manager dialog."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.registry import AUTH_POLICIES
from src.auth.store import AuthProfileStore
from src.config.settings import TaskConfig
from src.ui.workers.auth_worker import AuthWorker


_STATUS_TEXT = {
    AuthStatus.UNKNOWN: "未检查",
    AuthStatus.PROBING: "正在验证",
    AuthStatus.GUEST_OK: "游客可访问",
    AuthStatus.AUTH_REQUIRED: "需要登录",
    AuthStatus.CHALLENGE: "需要人工验证",
    AuthStatus.WAITING_USER: "等待人工操作",
    AuthStatus.VALIDATING: "正在复验",
    AuthStatus.VALID: "登录态有效",
    AuthStatus.EXPIRED: "登录态已过期",
    AuthStatus.INVALID_URL: "URL失效/页面为空",
    AuthStatus.ACCESS_BLOCKED: "访问受限",
    AuthStatus.ERROR: "验证失败",
}


class AuthManagerDialog(QDialog):
    def __init__(
        self,
        config: TaskConfig,
        store: AuthProfileStore,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store
        self._worker: AuthWorker | None = None
        self._rows = {
            policy.platform_key: index
            for index, policy in enumerate(AUTH_POLICIES)
        }
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.setWindowTitle("登录态管理中心")
        self.setObjectName("authManagerDialog")
        self.resize(1040, 680)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "先以游客模式验证；只有确实要求登录的平台才打开可视浏览器。"
            "验证码、密码和扫码只在平台页面中处理，不会写入日志。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        phone_row = QHBoxLayout()
        phone_row.addWidget(QLabel("手机号辅助填写"))
        self.phone_input = QLineEdit()
        self.phone_input.setObjectName("authPhoneInput")
        self.phone_input.setPlaceholderText("可选，仅保留在当前窗口内；不会自动发送验证码")
        self.phone_input.setMaxLength(32)
        phone_row.addWidget(self.phone_input, 1)
        layout.addLayout(phone_row)

        self.table = QTableWidget(len(AUTH_POLICIES), 6)
        self.table.setObjectName("authPlatformTable")
        self.table.setHorizontalHeaderLabels(
            ("平台", "状态", "账号", "最近验证", "错误码", "说明")
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        action_row = QHBoxLayout()
        self.probe_all_button = QPushButton("验证全部（游客优先）")
        self.probe_all_button.setObjectName("authProbeAllButton")
        self.probe_all_button.clicked.connect(
            lambda: self._start_action("probe_all")
        )
        self.probe_button = QPushButton("验证选中")
        self.probe_button.clicked.connect(lambda: self._start_action("probe"))
        self.login_button = QPushButton("登录 / 更新选中")
        self.login_button.setObjectName("authLoginButton")
        self.login_button.clicked.connect(lambda: self._start_action("login"))
        self.delete_button = QPushButton("删除选中登录态")
        self.delete_button.clicked.connect(self._delete_selected)
        self.cancel_button = QPushButton("停止")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        for button in (
            self.probe_all_button,
            self.probe_button,
            self.login_button,
            self.delete_button,
            self.cancel_button,
        ):
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("authStatusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def refresh(self) -> None:
        for row, policy in enumerate(AUTH_POLICIES):
            profile = self._store.profile_for(policy.platform_key)
            values = (
                policy.display_name,
                _STATUS_TEXT[profile.status],
                profile.masked_phone or "",
                profile.validated_at or "",
                profile.last_error_code or "",
                profile.last_message,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, policy.platform_key)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        if self.table.currentRow() < 0 and self.table.rowCount():
            self.table.selectRow(0)

    def _selected_platform(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return str(item.data(Qt.UserRole)) if item is not None else None

    def _start_action(self, action: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        platform_key = None if action == "probe_all" else self._selected_platform()
        if action != "probe_all" and platform_key is None:
            QMessageBox.information(self, "请选择平台", "请先选择一行。")
            return
        self._worker = AuthWorker(
            action,
            self._config,
            self._store,
            platform_key=platform_key,
            phone=self.phone_input.text().strip() or None,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.result_ready.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._set_running(True)
        self._worker.start()

    def _on_progress(self, platform_key: str, status: str, message: str) -> None:
        row = self._rows[platform_key]
        self.table.item(row, 1).setText(_STATUS_TEXT[AuthStatus(status)])
        self.table.item(row, 5).setText(message)
        self.status_label.setText(
            f"{self.table.item(row, 0).text()}：{message}"
        )
        self.table.selectRow(row)

    def _on_result(self, _result: AuthProbeResult) -> None:
        self.refresh()

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(f"操作失败：{message}")

    def _on_finished(self) -> None:
        self._set_running(False)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self.status_label.setText("验证完成。")

    def _delete_selected(self) -> None:
        platform_key = self._selected_platform()
        if platform_key is None:
            return
        profile = self._store.profile_for(platform_key)
        if profile.state_filename is None:
            return
        answer = QMessageBox.question(
            self,
            "删除登录态",
            "只删除所选平台的本机加密登录态，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._store.delete_state(platform_key)
            self.refresh()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在停止当前验证…")

    def _set_running(self, running: bool) -> None:
        self.probe_all_button.setEnabled(not running)
        self.probe_button.setEnabled(not running)
        self.login_button.setEnabled(not running)
        self.delete_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.phone_input.setEnabled(not running)
        self.table.setEnabled(not running)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self,
                "验证仍在运行",
                "请先点击“停止”，等待当前浏览器安全关闭。",
            )
            event.ignore()
            return
        event.accept()
