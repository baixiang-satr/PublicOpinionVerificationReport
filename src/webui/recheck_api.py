"""URL 有效性复验与批量删除的 js_api mixin（bridge.py 行数受限，独立成模块）。

宿主类需提供：``self._session()``、``self._sink``、``self._current_config``、
``self.auth``（AuthRunner，取登录态库）。
"""

from __future__ import annotations

from typing import Any

from src.services.url_recheck import UrlRecheckStore
from src.webui.recheck_runner import RecheckRunner


class RecheckApiMixin:
    def _recheck(self) -> RecheckRunner:
        runner = self.__dict__.get("_recheck_runner")
        if runner is None:
            runner = RecheckRunner(self._current_config, self._sink)
            self.__dict__["_recheck_runner"] = runner
        return runner

    def list_url_recheck(self) -> dict:
        """会话记录 ⨝ 复验结果的行列表（复验对话框数据源）。"""

        session = self._session()
        if session is None:
            return {"ok": False, "rows": [], "running": False}
        store = UrlRecheckStore(session.job_dir)
        rows = []
        for record in session.records():
            entry = store.get(record.task.evidence_id)
            rows.append(
                {
                    "eid": record.task.evidence_id,
                    "url": (
                        record.page.final_url or record.task.original_url or ""
                    ).strip(),
                    "status": record.status.value,
                    "recheck": entry.to_dict() if entry is not None else None,
                }
            )
        return {"ok": True, "rows": rows, "running": self._recheck().is_running()}

    def start_url_recheck(self) -> dict:
        session = self._session()
        if session is None:
            return {"ok": False, "message": "还没有打开的任务。"}
        ok, message = self._recheck().start(session, self.auth.store())
        return {"ok": ok, "message": message}

    def cancel_url_recheck(self) -> dict:
        self._recheck().cancel()
        return {"ok": True}

    def remove_records(self, evidence_ids: Any) -> dict:
        """批量删除记录（逐条走 ReviewSession.remove_record 的资产清理）。"""

        session = self._session()
        if session is None:
            return {"ok": False, "removed": 0}
        removed = 0
        for raw in evidence_ids or []:
            try:
                evidence_id = int(raw)
            except (TypeError, ValueError):
                continue
            if session.remove_record(evidence_id):
                removed += 1
        if removed:
            UrlRecheckStore(session.job_dir).prune(set(session.evidence_ids()))
            self._sink.emit("session", {})
        return {"ok": True, "removed": removed}


__all__ = ["RecheckApiMixin"]
