"""表格数据与人工补录的 js_api mixin（bridge.py 行数受限，逻辑独立成模块）。

宿主类需提供：``self._session()``（当前 ReviewSession 或 None）与
``self._sink``（事件出口）。
"""

from __future__ import annotations

from typing import Any

from src.services.invalid_urls import InvalidUrlStore
from src.services.manual_edit import validate_manual_edit
from src.webui.serialize import row_delta, sheet_payload


class ReviewApiMixin:
    _sink: Any = None
    _session: Any  # 宿主方法：() -> ReviewSession | None

    def get_sheet_payload(self) -> list[dict]:
        session = self._session()
        if session is None:
            return []
        return sheet_payload(session)

    def apply_edit(self, evidence_id: int, field: str, value: str) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False, "message": "还没有打开的任务。"}
        try:
            record = session.get_record(int(evidence_id))
        except KeyError:
            return {"ok": False, "message": f"记录 {evidence_id} 不存在，可能已被删除。"}
        try:
            validate_manual_edit(session.layout_for(record), str(field), str(value))
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        try:
            session.set_field(int(evidence_id), str(field), str(value))
        except Exception as error:  # noqa: BLE001 - 保存失败必须回执，前端据以回滚单元格
            return {"ok": False, "message": f"保存失败：{type(error).__name__}: {error}"}
        return {"ok": True, "row": row_delta(session, int(evidence_id)), "message": ""}

    def add_manual_row(self, sheet_name: str) -> dict:
        session = self._session()
        if session is None:
            return {"eid": None}
        try:
            record = session.add_manual_record(str(sheet_name))
        except KeyError:
            return {"eid": None}
        self._sink.emit("session", {})
        return {"eid": record.task.evidence_id}

    def remove_record(self, evidence_id: int) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False}
        ok = session.remove_record(int(evidence_id))
        if ok:
            # 单条删除同样联动清理失效候选，防止已删记录留在确认弹窗。
            InvalidUrlStore(session.job_dir).prune(set(session.evidence_ids()))
            self._sink.emit("session", {})
        return {"ok": ok}


__all__ = ["ReviewApiMixin"]
