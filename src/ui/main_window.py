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
        self.resize(1320, 920)
        self.setMinimumSize(1040, 800)
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))

        # 中央滚动区域 —— 解决输出结果看不见的问题
        scroll = QScrollArea()
        scroll.setObjectName("mainScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        self.setCentralWidget(scroll)

        root = QWidget()
        root.setObjectName("appRoot")
        scroll.setWidget(root)

        # 主布局 —— 宽松舒适
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(16)

        # ── 顶部标题区（渐变背景卡片） ──
        header_card = QWidget()
        header_card.setObjectName("headerCard")
        header_card.setStyleSheet(
            "QWidget#headerCard { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            "stop:0 #0d3b35, stop:1 #1a7a64); border-radius: 12px; padding: 20px 24px; }"
        )
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(24, 16, 24, 16)
        header_layout.setSpacing(4)

        title = QLabel("舆情验证报告")
        title.setObjectName("appTitle")
        title.setStyleSheet("color: #ffffff; font-size: 28px; font-weight: 700;")
        subtitle = QLabel("批量读取网页 → 自动整理证据 → 生成固定格式 template.zip")
        subtitle.setObjectName("appSubtitle")
        subtitle.setStyleSheet("color: #b8dfd2; font-size: 15px;")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addWidget(header_card)

        # ── 操作提示条 ──
        notice = QLabel(
            "按下方步骤操作即可。程序只写任务副本，不会修改 template 原件。"
            "登录或验证码页面不会被当作证据。需要登录时，请取消勾选「后台运行浏览器」"
            "并在弹出的可视窗口中手工完成登录。"
        )
        notice.setObjectName("stepNotice")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        # ════════════════════════════════════════════
        # 第 1 步：选择文件
        # ════════════════════════════════════════════
        step1 = QGroupBox("第 1 步：选择包含网页链接的文件")
        step1.setMinimumHeight(120)
        step1_layout = QVBoxLayout(step1)
        step1_layout.setContentsMargins(16, 22, 16, 16)
        step1_layout.setSpacing(8)
        self.file_selector = FileSelector()
        step1_layout.addWidget(self.file_selector)
        layout.addWidget(step1)

        # ════════════════════════════════════════════
        # 第 2 步：检查设置
        # ════════════════════════════════════════════
        step2 = QGroupBox("第 2 步：检查运行设置（鼠标悬停可查看各参数说明）")
        step2.setMinimumHeight(180)
        step2_layout = QVBoxLayout(step2)
        step2_layout.setContentsMargins(16, 22, 16, 16)
        step2_layout.setSpacing(8)
        self.options = TaskOptionsWidget(self._base_config.task)
        step2_layout.addWidget(self.options)
        layout.addWidget(step2)

        # ════════════════════════════════════════════
        # 第 3 步：操作按钮区
        # ════════════════════════════════════════════
        step3_group = QGroupBox("第 3 步：开始运行")
        step3_layout = QVBoxLayout(step3_group)
        step3_layout.setContentsMargins(16, 22, 16, 16)
        step3_layout.setSpacing(10)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self.start_button = QPushButton("▶  开始生成")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setMinimumWidth(160)
        self.start_button.clicked.connect(self._start_from_file)

        self.cancel_button = QPushButton("■  取消任务")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setMinimumWidth(120)
        self.cancel_button.clicked.connect(self._cancel_job)

        self.retry_button = QPushButton("↻  重试失败项")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.setMinimumWidth(120)
        self.retry_button.clicked.connect(self._retry_failed)

        self.open_output_button = QPushButton("◉  打开输出位置")
        self.open_output_button.setObjectName("openOutputButton")
        self.open_output_button.setMinimumWidth(120)
        self.open_output_button.clicked.connect(self._open_output)

        action_row.addWidget(self.start_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.retry_button)
        action_row.addWidget(self.open_output_button)
        action_row.addStretch(1)

        step3_layout.addLayout(action_row)

        action_hint = QLabel("运行中可随时取消；取消后不会生成不完整压缩包。")
        action_hint.setProperty("muted", True)
        step3_layout.addWidget(action_hint)

        layout.addWidget(step3_group)

        # ════════════════════════════════════════════
        # 进度区域
        # ════════════════════════════════════════════
        progress_group = QGroupBox("任务进度")
        progress_group.setMinimumHeight(200)
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(16, 22, 16, 16)
        progress_layout.setSpacing(10)
        self.progress_panel = ProgressPanel()
        progress_layout.addWidget(self.progress_panel)
        layout.addWidget(progress_group)

        # ════════════════════════════════════════════
        # 详情标签页（结果 + 日志）
        # ════════════════════════════════════════════
        self.result_table = ResultTable()
        self.log_viewer = LogViewer()
        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        self.tabs.setMinimumHeight(320)
        self.tabs.addTab(self.result_table, "抓取结果")
        self.tabs.addTab(self.log_viewer, "运行日志")
        layout.addWidget(self.tabs, 1)

        self.statusBar().showMessage("请先选择 URL 文件，然后点击「开始生成」")

    # ── 启动任务 ──
    def _start_from_file(self) -> None:
        input_path = self.file_selector.path()
        if input_path is None:
            QMessageBox.information(
                self,
                "还差一步",
                "请先在「第 1 步」选择 TXT、CSV 或普通 XLSX 文件。",
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
        self.statusBar().showMessage("任务已提交，正在准备浏览器环境…")
        # 延迟启动让 UI 先刷新、用户看到反馈
        QTimer.singleShot(150, self._worker.start)
        self.log_viewer.append_message("INFO", "任务已提交，正在初始化浏览器环境，请稍候…")

    def _cancel_job(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("⋯ 正在取消…")
        self.statusBar().showMessage("已收到取消请求，正在安全关闭浏览器…")
        self.log_viewer.append_message("WARNING", "已请求取消，当前操作结束后将安全停止。")
        self._worker.cancel()

    def _on_job_started(self, summary: JobSummary) -> None:
        rejected = f"，忽略 {summary.rejected_count} 个重复或无效值" if summary.rejected_count else ""
        self.statusBar().showMessage(f"任务 {summary.job_id}：共 {summary.total} 条 URL{rejected}")
        self.log_viewer.append_message("INFO", f"任务启动成功，共 {summary.total} 条 URL{rejected}")

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
                f"template.zip 已成功生成。\n\n位置：\n{result.archive_path}",
            )
        elif result.cancelled:
            self.statusBar().showMessage("任务已取消，未生成压缩包")
        else:
            self.statusBar().showMessage("没有记录满足模板要求，未生成空压缩包")
            QMessageBox.warning(
                self,
                "未生成压缩包",
                "本次没有可安全导出的记录。请切换到「抓取结果」标签页查看待补录和失败原因，"
                "处理登录态或网络问题后可点击「重试失败项」。",
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
        self.cancel_button.setText("■  取消任务")
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
