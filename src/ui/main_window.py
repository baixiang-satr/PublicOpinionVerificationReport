"""Main desktop workflow for creating the fixed template.zip deliverable."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QTimer, QUrl, Qt
from PyQt5.QtGui import QCloseEvent, QDesktopServices
from PyQt5.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
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
        self.resize(960, 740)
        self.setMinimumSize(860, 660)
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))

        # 中央滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        self.setCentralWidget(scroll)

        root = QWidget()
        root.setObjectName("appRoot")
        scroll.setWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # ── 标题 ──
        title = QLabel("舆情验证报告")
        title.setObjectName("appTitle")
        subtitle = QLabel("批量读取网页，自动整理证据，生成固定格式 template.zip")
        subtitle.setObjectName("appSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # ── 提示 ──
        notice = QLabel(
            "程序只写任务副本，不会修改 template 原件。"
            "需要登录的页面请取消勾选「后台运行浏览器」，在弹出窗口中手工完成登录。"
        )
        notice.setObjectName("stepNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        # ── 第 1 步：文件 ──
        g1 = QGroupBox("1. 选择 URL 文件")
        g1.setMinimumHeight(100)
        g1_layout = QVBoxLayout(g1)
        g1_layout.setContentsMargins(12, 18, 12, 12)
        self.file_selector = FileSelector()
        g1_layout.addWidget(self.file_selector)
        layout.addWidget(g1)

        # ── 第 2 步：参数 ──
        g2 = QGroupBox("2. 运行参数（鼠标悬停看说明）")
        g2_layout = QVBoxLayout(g2)
        g2_layout.setContentsMargins(12, 18, 12, 12)
        self.options = TaskOptionsWidget(self._base_config.task)
        g2_layout.addWidget(self.options)
        layout.addWidget(g2)

        # ── 第 3 步：操作 ──
        g3 = QGroupBox("3. 执行")
        g3_layout = QVBoxLayout(g3)
        g3_layout.setContentsMargins(12, 18, 12, 12)
        g3_layout.setSpacing(8)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.start_button = QPushButton()
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.start_button.setText("开始生成")
        self.start_button.setMinimumWidth(130)
        self.start_button.clicked.connect(self._start_from_file)

        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.cancel_button.setText("  取消任务")
        self.cancel_button.clicked.connect(self._cancel_job)

        self.retry_button = QPushButton()
        self.retry_button.setObjectName("retryButton")
        self.retry_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.retry_button.setText("重试失败项")
        self.retry_button.clicked.connect(self._retry_failed)

        self.open_output_button = QPushButton()
        self.open_output_button.setObjectName("openOutputButton")
        self.open_output_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.open_output_button.setText("打开输出位置")
        self.open_output_button.clicked.connect(self._open_output)

        action_row.addWidget(self.start_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.retry_button)
        action_row.addWidget(self.open_output_button)
        action_row.addStretch(1)
        g3_layout.addLayout(action_row)

        hint = QLabel("运行中可随时取消，取消后不会生成不完整压缩包。")
        hint.setProperty("muted", True)
        g3_layout.addWidget(hint)
        layout.addWidget(g3)

        # ── 进度 ──
        g4 = QGroupBox("进度")
        g4_layout = QVBoxLayout(g4)
        g4_layout.setContentsMargins(12, 18, 12, 12)
        self.progress_panel = ProgressPanel()
        g4_layout.addWidget(self.progress_panel)
        layout.addWidget(g4)

        # ── 结果 / 日志 标签页 ──
        self.result_table = ResultTable()
        self.log_viewer = LogViewer()
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(220)
        self.tabs.addTab(self.result_table, "抓取结果")
        self.tabs.addTab(self.log_viewer, "运行日志")
        layout.addWidget(self.tabs, 1)

        self.statusBar().showMessage("就绪 — 请选择 URL 文件后点击「开始生成」")

    # ── 动作 ──
    def _start_from_file(self) -> None:
        input_path = self.file_selector.path()
        if input_path is None:
            QMessageBox.information(self, "还差一步", "请先选择 TXT、CSV 或 XLSX 文件。")
            return
        request = JobRequest(input_path=input_path)
        self._start_worker(request, clear_results=True)

    def _retry_failed(self) -> None:
        if self._last_result is None or not self._last_result.retryable_tasks:
            QMessageBox.information(self, "没有失败项", "当前没有可重试的记录。")
            return
        request = JobRequest(
            tasks=self._last_result.retryable_tasks,
            retained_records=tuple(
                r for r in self._last_result.records if r.status.value == "exported"
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
        self.statusBar().showMessage("正在准备，请稍候…")
        self.log_viewer.append_message("INFO", "任务已提交，正在初始化…")
        QTimer.singleShot(150, self._worker.start)

    def _cancel_job(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("正在取消…")
        self.statusBar().showMessage("正在安全关闭浏览器…")
        self.log_viewer.append_message("WARNING", "已请求取消。")
        self._worker.cancel()

    def _on_job_started(self, summary: JobSummary) -> None:
        rejected = f"，忽略 {summary.rejected_count} 项" if summary.rejected_count else ""
        self.statusBar().showMessage(f"共 {summary.total} 条 URL{rejected}")
        self.log_viewer.append_message("INFO", f"启动成功，共 {summary.total} 条 URL{rejected}")

    def _on_job_finished(self, result: JobResult) -> None:
        self._last_result = result
        self._last_output = result.archive_path or result.job_dir
        for record in result.records:
            self.result_table.set_record(record)
        self._set_running(False)
        if result.archive_path is not None:
            self.statusBar().showMessage(f"已生成：{result.archive_path}")
            QMessageBox.information(self, "完成", f"template.zip 已生成。\n\n位置：\n{result.archive_path}")
        elif result.cancelled:
            self.statusBar().showMessage("已取消")
        else:
            self.statusBar().showMessage("未生成压缩包")
            QMessageBox.warning(self, "未生成", "没有可安全导出的记录。请查看「抓取结果」后重试。")

    def _on_job_failed(self, message: str) -> None:
        self._set_running(False)
        self.tabs.setCurrentIndex(1)
        self.log_viewer.append_message("ERROR", message)
        self.statusBar().showMessage("任务失败")
        QMessageBox.critical(self, "任务未完成", f"{message}\n\n未生成压缩包，请检查后重试。")

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
            self, "任务仍在运行", "关闭前需要先取消任务。现在取消吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._cancel_job()
        event.ignore()
