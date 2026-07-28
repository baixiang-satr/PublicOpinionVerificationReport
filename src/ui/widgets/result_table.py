"""Read-only runtime audit table for crawl records."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from src.domain.models import RecordResult, RecordStatus


HEADERS = (
    "编号",
    "原始 URL",
    "最终 URL",
    "标题",
    "作者/账号",
    "作者主页",
    "工作表",
    "HTTP状态码",
    "处理状态",
    "错误与提醒",
)

STATUS_TEXT = {
    RecordStatus.PENDING: "⏳ 等待中",
    RecordStatus.RUNNING: "▶ 处理中",
    RecordStatus.CRAWLED: "✓ 已抓取",
    RecordStatus.ROUTED: "✓ 已路由",
    RecordStatus.ASSETS_READY: "✓ 资产就绪",
    RecordStatus.READY_FOR_EXPORT: "✓ 可导出",
    RecordStatus.NEEDS_REVIEW: "⚠ 待人工补录",
    RecordStatus.FAILED: "✗ 失败",
    RecordStatus.CANCELLED: "– 已取消",
    RecordStatus.EXPORTED: "✓ 已导出",
}

STATUS_COLORS = {
    RecordStatus.EXPORTED: QColor("#d9eee8"),
    RecordStatus.READY_FOR_EXPORT: QColor("#d9eee8"),
    RecordStatus.ASSETS_READY: QColor("#d9eee8"),
    RecordStatus.NEEDS_REVIEW: QColor("#fff0d5"),
    RecordStatus.FAILED: QColor("#f8dddd"),
    RecordStatus.CANCELLED: QColor("#e5e9ed"),
}


class ResultTable(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(0, len(HEADERS), parent)
        self.setObjectName("resultTable")
        self.setHorizontalHeaderLabels(HEADERS)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((60, 220, 220, 180, 120, 200, 100, 70, 90)):
            self.setColumnWidth(column, width)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.horizontalHeader().setMinimumSectionSize(50)
        self._rows: dict[int, int] = {}
        self._records: dict[int, RecordResult] = {}

    def set_record(self, record: RecordResult) -> None:
        evidence_id = record.task.evidence_id
        row = self._rows.get(evidence_id)
        if row is None:
            row = self.rowCount()
            self.insertRow(row)
            self._rows[evidence_id] = row
        self._records[evidence_id] = record
        values = (
            f"{evidence_id:03d}",
            record.task.original_url,
            record.page.final_url or "",
            record.page.title or "",
            record.page.author_name or record.page.author_id or "",
            record.page.author_url or "",
            record.route.sheet_name if record.route else "",
            "" if record.page.status_code is None else str(record.page.status_code),
            STATUS_TEXT.get(record.status, record.status.value),
            _error_text(record),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column in {0, 7, 8}:
                item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, column, item)
        color = STATUS_COLORS.get(record.status)
        if color is not None:
            self.item(row, 8).setBackground(color)

    def records(self) -> tuple[RecordResult, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def clear_records(self) -> None:
        self.setRowCount(0)
        self._rows.clear()
        self._records.clear()


def _error_text(record: RecordResult) -> str:
    return "；".join(f"{error.code}: {error.message}" for error in record.errors)
