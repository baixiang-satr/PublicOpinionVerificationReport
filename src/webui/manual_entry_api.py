"""待补录/未收录清单的 js_api mixin：表格展示数据源 + CSV 导出。

宿主类需提供：``self.jobs``（JobRunner，含 session）、``self._window_provider``。
清单文件为任务目录下的 ``pending_manual_entry.csv``（质量报告产物之一，
含抓取失败/待补记录与输入阶段被拒链接）。
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Any

MANUAL_ENTRY_FILE_NAME = "pending_manual_entry.csv"


class ManualEntryApiMixin:
    jobs: Any = None
    _window_provider: Any = None

    def _manual_entry_csv(self) -> Path | None:
        session = getattr(self.jobs, "session", None)
        if session is None:
            return None
        path = Path(session.job_dir) / MANUAL_ENTRY_FILE_NAME
        return path if path.is_file() else None

    def list_manual_entries(self) -> dict:
        """读取当前任务目录的待补录/未收录清单（含输入阶段被拒链接）。"""

        path = self._manual_entry_csv()
        if path is None:
            return {"ok": False, "rows": [], "path": "", "message": "当前任务还没有生成清单。"}
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = [dict(row) for row in csv.DictReader(stream)]
        except OSError as error:
            return {"ok": False, "rows": [], "path": str(path), "message": f"清单读取失败：{error}"}
        return {"ok": True, "rows": rows, "path": str(path), "message": ""}

    def export_manual_entries(self) -> dict:
        """把待补录清单 CSV 复制到用户选择的位置。"""

        import webview

        source = self._manual_entry_csv()
        if source is None:
            return {"ok": False, "message": "当前任务还没有生成清单。"}
        window = (
            self._window_provider()
            if self._window_provider
            else webview.windows[0]
        )
        result = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=MANUAL_ENTRY_FILE_NAME,
            file_types=("CSV 表格 (*.csv)",),
        )
        if not result:
            return {"ok": False, "message": ""}
        target = Path(result if isinstance(result, str) else result[0])
        if target.suffix.lower() != ".csv":
            target = target.with_suffix(".csv")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as error:
            return {"ok": False, "message": f"导出失败：{error}"}
        return {"ok": True, "message": f"清单已导出：{target}"}


__all__ = ["ManualEntryApiMixin"]
