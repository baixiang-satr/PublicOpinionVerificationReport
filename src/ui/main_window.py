"""Main desktop workflow for creating the fixed template.zip deliverable."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtGui import QCloseEvent, QDesktopServices
from PyQt5.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import AppConfig
from src.services.models import JobRequest, JobResult, JobSummary
from src.ui.widgets.file_selector import FileSelector
from src.ui.widgets.log_viewer import LogViewer
from src.ui.widgets.progress_panel import ProgressPanel
from src.ui.widgets.result_table import ResultTable
from src.ui.widgets.task_options import TaskOptionsWidget
from src.ui.workers.task_worker import TaskWorker


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__()
        self._base_config = config or AppConfig.from_environment()
        self._worker: TaskWorker | None = None
        self._last_result: JobResult | None = None
        self._last_output: Path | None = None
        self._build_ui()
        self._set_running(False)

    def _build_ui(self) -> None:
        self.setWindowTitle("舆情验证报告工具")
        self.resize(1280, 900)
        self.setMinimumSize(980, 800)
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(10)

        title = QLabel("舆情验证报告")
        title.setObjectName("appTitle")
        subtitle = QLabel("批量读取网页，自动整理证据，并生成固定格式的 template.zip")
        subtitle.setObjectName("appSubtitle")
        header = QVBoxLayout()
        header.setSpacing(1)
        header.addWidget(title)
        header.addWidget(subtitle)
        layout.addLayout(header)

        notice = QLabel(
            "按下面 3 步操作即可。程序只写任务副本，不会修改 template 原件；"
            "登录或验证码页面不会被当作证据。需要登录时选择自己的登录态，"
            "或取消“后台运行浏览器”后在可视窗口中手工完成。"
        )
        notice.setObjectName("stepNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        input_group = QGroupBox("第 1 步  选择包含网页链接的文件")
        input_group.setMinimumHeight(112)
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(10, 10, 10, 8)
        self.file_selector = FileSelector()
        input_layout.addWidget(self.file_selector)
        layout.addWidget(input_group)

        options_group = QGroupBox("第 2 步  检查设置（不确定时保持默认）")
        options_group.setMinimumHeight(158)
        options_layout = QVBoxLayout(options_group)
        options_layout.setContentsMargins(10, 10, 10, 8)
        self.options = TaskOptionsWidget(self._base_config.task)
        options_layout.addWidget(self.options)
        layout.addWidget(options_group)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_label = QLabel("第 3 步")
        action_label.setStyleSheet("font-weight: 700; color: #263747;")
        self.start_button = QPushButton("开始生成")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.start_button.clicked.connect(self._start_from_file)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.cancel_button.clicked.connect(self._cancel_job)
        self.retry_button = QPushButton("重试失败项")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.retry_button.clicked.connect(self._retry_failed)
        self.open_output_button = QPushButton("打开输出位置")
        self.open_output_button.setObjectName("openOutputButton")
        self.open_output_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.open_output_button.clicked.connect(self._open_output)
        action_hint = QLabel("运行中可随时取消；取消后不会生成不完整压缩包。")
        action_hint.setProperty("muted", True)
        action_row.addWidget(action_label)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.retry_button)
        action_row.addWidget(self.open_output_button)
        action_row.addWidget(action_hint, 1)
        layout.addLayout(action_row)

        progress_group = QGroupBox("任务进度")
        progress_group.setMinimumHeight(180)
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(10, 10, 10, 9)
        self.progress_panel = ProgressPanel()
        progress_layout.addWidget(self.progress_panel)
        layout.addWidget(progress_group)

        self.result_table = ResultTable()
        self.log_viewer = LogViewer()
        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        self.tabs.addTab(self.result_table, "抓取结果")
        self.tabs.addTab(self.log_viewer, "运行日志")
        layout.addWidget(self.tabs, 1)
        self.statusBar().showMessage("请先选择 URL 文件")

    def _start_from_file(self) -> None:
        input_path = self.file_selector.path()
        if input_path is None:
            QMessageBox.information(
                self,
                "还差一步",
                "请先在“第 1 步”选择 TXT、CSV 或普通 XLSX 文件。",
            )
            return
        request = JobRequest(input_path=input_path)
        self._start_worker(request, clear_results=True)

    def _retry_failed(self) -> None:
        if self._last_result is None or not self._last_result.retryable_tasks:
            QMessageBox.information(self, "没有失败项", "当前没有可重试的失败或待补录记录。")
            return
        request = JobRequest(
            tasks=self._last_result.retryable_tasks,
            retained_records=tuple(
                record
                for record in self._last_result.records
                if record.status.value == "exported"
            ),
            label="失败项重试",
        )
        self._start_worker(request, clear_results=True)

    def _start_worker(self, request: JobRequest, *, clear_results: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            task_config = self.options.task_config()
        except ValueError as error:
            QMessageBox.warning(self, "设置需要检查", str(error))
            return
        config = AppConfig(template=self._base_config.template, task=task_config)
        if clear_results:
            self.result_table.clear_records()
            self.progress_panel.reset()
            self.log_viewer.clear()
        self._last_result = None
        self._last_output = None
        self.tabs.setCurrentIndex(0)
        self._worker = TaskWorker(config, request, self)
        self._worker.job_started.connect(self._on_job_started)
        self._worker.record_updated.connect(self.result_table.set_record)
        self._worker.progress_changed.connect(self.progress_panel.set_snapshot)
        self._worker.log_message.connect(self.log_viewer.append_event)
        self._worker.job_finished.connect(self._on_job_finished)
        self._worker.job_failed.connect(self._on_job_failed)
        self._worker.finished.connect(self._on_worker_stopped)
        self._set_running(True)
        self.statusBar().showMessage("正在准备任务，请稍候…")
        self._worker.start()

    def _cancel_job(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("正在取消…")
        self.statusBar().showMessage("已收到取消请求，正在安全关闭浏览器…")
        self.log_viewer.append_message("WARNING", "已请求取消，当前操作结束后将安全停止。")
        self._worker.cancel()

    def _on_job_started(self, summary: JobSummary) -> None:
        rejected = f"，忽略 {summary.rejected_count} 个重复或无效值" if summary.rejected_count else ""
        self.statusBar().showMessage(f"任务 {summary.job_id}：共 {summary.total} 条 URL{rejected}")

    def _on_job_finished(self, result: JobResult) -> None:
        self._last_result = result
        self._last_output = result.archive_path or result.job_dir
        for record in result.records:
            self.result_table.set_record(record)
        self._set_running(False)
        if result.archive_path is not None:
            self.statusBar().showMessage(f"已生成：{result.archive_path}")
            QMessageBox.information(
                self,
                "生成完成",
                f"template.zip 已生成。\n\n位置：\n{result.archive_path}",
            )
        elif result.cancelled:
            self.statusBar().showMessage("任务已取消，未生成压缩包")
        else:
            self.statusBar().showMessage("没有记录满足模板要求，未生成空压缩包")
            QMessageBox.warning(
                self,
                "未生成压缩包",
                "本次没有可安全导出的记录。请在“抓取结果”中查看待补录和失败原因，"
                "处理登录态或网络问题后可点击“重试失败项”。",
            )

    def _on_job_failed(self, message: str) -> None:
        self._set_running(False)
        self.tabs.setCurrentIndex(1)
        self.log_viewer.append_message("ERROR", message)
        self.statusBar().showMessage("任务失败，未生成压缩包")
        QMessageBox.critical(
            self,
            "任务未完成",
            f"{message}\n\n没有生成不完整的 template.zip，请根据提示检查后重试。",
        )

    def _on_worker_stopped(self) -> None:
        self._set_running(False)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_running(self, running: bool) -> None:
        self.file_selector.set_controls_enabled(not running)
        self.options.set_controls_enabled(not running)
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setText("取消任务")
        has_retry = self._last_result is not None and bool(self._last_result.retryable_tasks)
        self.retry_button.setEnabled(not running and has_retry)
        self.open_output_button.setEnabled(not running and self._last_output is not None)

    def _open_output(self) -> None:
        if self._last_output is None:
            return
        target = self._last_output if self._last_output.is_dir() else self._last_output.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is None or not self._worker.isRunning():
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "任务仍在运行",
            "关闭前需要先安全取消任务。现在取消吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._cancel_job()
        event.ignore()
