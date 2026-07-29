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
    "主页证据",
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
        for column, width in enumerate((60, 220, 220, 180, 120, 200, 100, 70, 90, 90)):
            self.setColumnWidth(column, width)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(10, QHeaderView.Stretch)
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
            _author_evidence_text(record),
            _error_text(record),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(_cell_tooltip(record, column, value))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column in {0, 7, 8, 9}:
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


def _author_evidence_text(record: RecordResult) -> str:
    """Author-home evidence state: accepted / rejected-with-code / candidate."""

    if record.assets.author_screenshot is not None:
        return "✓ 已接受"
    rejection = next(
        (
            error.code
            for error in record.errors
            if error.code.startswith("AUTHOR_")
        ),
        None,
    )
    if rejection is not None:
        return f"✗ {rejection}"
    if record.page.author_url:
        return "… 候选"
    return ""


def _cell_tooltip(record: RecordResult, column: int, value: str) -> str:
    """Field source/confidence provenance on hover for extracted cells."""

    field_by_column = {3: "title", 4: "author_name", 5: "author_url"}
    field = field_by_column.get(column)
    if field is None:
        return value
    source = record.page.field_sources.get(field)
    confidence = record.page.field_confidences.get(field)
    parts = [part for part in (value, f"来源：{source.value}" if source else None,
                               f"置信度：{confidence:.2f}" if confidence else None) if part]
    return "\n".join(parts)
