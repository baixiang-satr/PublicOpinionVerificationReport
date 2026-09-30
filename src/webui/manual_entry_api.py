"""待补录/未收录清单的 js_api mixin：表格展示数据源 + CSV 导出。

宿主类需提供：``self.jobs``（JobRunner，含 session）。
清单文件为任务目录下的 ``pending_manual_entry.csv``（质量报告产物之一，
含抓取失败/待补记录与输入阶段被拒链接）。

CSV 是抓取结束时的静态快照，补录不会回写它；因此读取时按工作台实时
缺失判定过滤——已补录完成（或记录已删除）的条目不再显示，只计入
``completed_count``。输入被拒行没有证据编号，无法自动判定，始终保留。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

MANUAL_ENTRY_FILE_NAME = "pending_manual_entry.csv"


class ManualEntryApiMixin:
    jobs: Any = None

    def _manual_entry_csv(self) -> Path | None:
        session = getattr(self.jobs, "session", None)
        if session is None:
            return None
        path = Path(session.job_dir) / MANUAL_ENTRY_FILE_NAME
        return path if path.is_file() else None

    @staticmethod
    def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = list(reader.fieldnames or ())
            rows = [dict(row) for row in reader]
        return fieldnames, rows

    def _filter_completed(
        self,
        rows: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], int]:
        """剔除已补录完成/记录已删除的行；无法判定的行（如输入被拒）保留。"""

        session = getattr(self.jobs, "session", None)
        get_record = getattr(session, "get_record", None)
        missing_labels = getattr(session, "missing_labels", None)
        get_override = getattr(session, "get_override", None)
        if not callable(get_record) or not callable(missing_labels):
            return rows, 0
        kept: list[dict[str, str]] = []
        completed = 0
        for row in rows:
            evidence_id = _parse_evidence_id(row.get("证据编号", ""))
            if evidence_id is None:
                kept.append(row)  # 输入被拒行没有编号，无法自动判定
                continue
            try:
                record = get_record(evidence_id)
            except KeyError:
                completed += 1  # 记录已被删除，清单同步移除
                continue
            override = get_override(evidence_id) if callable(get_override) else None
            if missing_labels(record, override):
                kept.append(row)
            else:
                completed += 1
        return kept, completed

    def list_manual_entries(self) -> dict:
        """读取当前任务目录的待补录/未收录清单（实时过滤已补录完成行）。"""

        path = self._manual_entry_csv()
        if path is None:
            return {
                "ok": False,
                "rows": [],
                "path": "",
                "completed_count": 0,
                "message": "当前任务还没有生成清单。",
            }
        try:
            _fieldnames, rows = self._read_rows(path)
        except OSError as error:
            return {
                "ok": False,
                "rows": [],
                "path": str(path),
                "completed_count": 0,
                "message": f"清单读取失败：{error}",
            }
        rows, completed = self._filter_completed(rows)
        return {
            "ok": True,
            "rows": rows,
            "path": str(path),
            "completed_count": completed,
            "message": "",
        }

    def dump_manual_entries_csv(self, target_path: str) -> dict:
        """把待补录清单（实时过滤已补录完成行）写到 *target_path*。

        B/S 模式下由 ``GET /api/download/manual-entries`` 写入临时文件后回传下载。
        """

        source = self._manual_entry_csv()
        if source is None:
            return {"ok": False, "message": "当前任务还没有生成清单。"}
        target = Path(target_path)
        if target.suffix.lower() != ".csv":
            target = target.with_suffix(".csv")
        try:
            fieldnames, rows = self._read_rows(source)
        except OSError as error:
            return {"ok": False, "message": f"清单读取失败：{error}"}
        rows, completed = self._filter_completed(rows)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as error:
            return {"ok": False, "message": f"导出失败：{error}"}
        message = f"清单已导出：{target}"
        if completed:
            message += f"（{completed} 条已补录完成，未包含）"
        return {"ok": True, "message": message}


def _parse_evidence_id(text: object) -> int | None:
    cleaned = str(text or "").strip()
    return int(cleaned) if cleaned.isdigit() else None


__all__ = ["ManualEntryApiMixin"]
