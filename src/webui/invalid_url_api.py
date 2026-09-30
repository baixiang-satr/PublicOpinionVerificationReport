"""URL 失效候选确认弹窗的 js_api mixin（bridge.py 行数受限，独立成模块）。

抓取完成后前端自动弹出确认框：列出抓取中判定失效且尚无用户决策的记录，
「保留」经 ``keep_invalid_url_candidates`` 落盘留痕；「删除选中记录」复用
``RecheckApiMixin.remove_records``（资产清理 + 候选 prune + deleted 留痕）。

宿主类需提供：``self._session()``（当前 ReviewSession 或 None）。
"""

from __future__ import annotations

from typing import Any

from src.services.invalid_urls import DECISION_KEEP, InvalidUrlStore


class InvalidUrlApiMixin:
    def list_invalid_url_candidates(self) -> dict:
        """抓取中判定失效且尚无用户决策的记录（确认弹窗数据源）。"""

        session = self._session()
        if session is None:
            return {"ok": False, "rows": []}
        pending = InvalidUrlStore(session.job_dir).pending()
        rows = []
        for record in session.records():
            entry = pending.get(record.task.evidence_id)
            if entry is None:
                continue
            rows.append(
                {
                    "eid": record.task.evidence_id,
                    "url": (
                        record.page.final_url or record.task.original_url or ""
                    ).strip(),
                    "code": entry.code,
                    "message": entry.message,
                    "checked_at": entry.checked_at,
                }
            )
        return {"ok": True, "rows": rows}

    def keep_invalid_url_candidates(self, evidence_ids: Any) -> dict:
        """用户选择「保留」：为指定候选追加 keep 决策留痕（幂等）。"""

        session = self._session()
        if session is None:
            return {"ok": False, "kept": 0}
        store = InvalidUrlStore(session.job_dir)
        pending = store.pending()
        wanted: set[int] = set()
        for raw in evidence_ids or []:
            try:
                wanted.add(int(raw))
            except (TypeError, ValueError):
                continue
        urls = {
            record.task.evidence_id: (
                record.page.final_url or record.task.original_url or ""
            ).strip()
            for record in session.records()
        }
        kept = store.record_decisions(
            sorted(eid for eid in pending if eid in wanted),
            DECISION_KEEP,
            lambda eid: urls.get(eid, ""),
        )
        return {"ok": True, "kept": kept}


__all__ = ["InvalidUrlApiMixin"]
