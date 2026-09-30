"""URL 失效候选：抓取中判定结果的持久化与用户决策留痕。

抓取流程判为失效的 URL（规则确证三码 + 大模型逐字引用确证）按
``evidence_id`` 持久化到任务目录：

- ``invalid_urls.json``：候选表，结构复用 ``RecheckEntry``
  （status 恒 ``invalid``/code/message=引文/checked_at），记录删除时用
  ``prune``/``discard`` 联动清理，断点续跑后仍可读取；
- ``invalid_url_decisions.json``：决策留痕（只增不减）——用户在确认弹窗中
  的每次「保留/删除」都记录引文快照与两个时间戳（判定时间 + 决策时间）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from src.domain.models import RecordResult, RecordStatus
from src.services.url_recheck import RecheckEntry, RecheckStatus

logger = logging.getLogger(__name__)

INVALID_URLS_FILENAME = "invalid_urls.json"
INVALID_URL_DECISIONS_FILENAME = "invalid_url_decisions.json"

DECISION_KEEP = "keep"
DECISION_DELETED = "deleted"

#: 候选成立的终态：删除类屏障 FAILED（404/内容不可用/LLM 确证）或 NEEDS_REVIEW
#: （重定向首页）。续跑/重试后转为成功态的记录的候选会被 discard 清理。
_CANDIDATE_STATUSES = {RecordStatus.FAILED, RecordStatus.NEEDS_REVIEW}


class InvalidUrlStore:
    """``job_dir/invalid_urls.json`` 候选表 + ``invalid_url_decisions.json`` 留痕。"""

    def __init__(self, job_dir: Path) -> None:
        self._dir = Path(job_dir)
        self._path = self._dir / INVALID_URLS_FILENAME
        self._decisions_path = self._dir / INVALID_URL_DECISIONS_FILENAME
        self._entries: dict[int, RecheckEntry] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key, values in raw.items():
            try:
                evidence_id = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(values, dict):
                self._entries[evidence_id] = RecheckEntry.from_dict(values)

    def _save(self) -> None:
        payload = {
            str(evidence_id): entry.to_dict()
            for evidence_id, entry in sorted(self._entries.items())
        }
        try:
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError as error:
            logger.warning("Unable to persist invalid URL candidates %s: %s", self._path, error)

    def get(self, evidence_id: int) -> RecheckEntry | None:
        return self._entries.get(int(evidence_id))

    def all(self) -> dict[int, RecheckEntry]:
        return dict(self._entries)

    def set(self, evidence_id: int, code: str = "", message: str = "") -> RecheckEntry:
        entry = RecheckEntry(
            RecheckStatus.INVALID,
            code,
            message,
            datetime.now().astimezone().isoformat(),
        )
        self._entries[int(evidence_id)] = entry
        self._save()
        return entry

    def resolved_eids(self) -> set[int]:
        """已有最终决策（保留/删除）的记录编号。"""

        return {int(item.get("eid")) for item in self._read_decisions() if _is_int_like(item.get("eid"))}

    def pending(self) -> dict[int, RecheckEntry]:
        """尚无用户决策的候选（确认弹窗的数据源）。"""

        resolved = self.resolved_eids()
        return {
            evidence_id: entry
            for evidence_id, entry in self._entries.items()
            if evidence_id not in resolved
        }

    def record_decisions(
        self,
        evidence_ids: Iterable[int],
        decision: str,
        url_lookup: Callable[[int], str] | None = None,
    ) -> int:
        """追加决策留痕（含引文快照与判定时间）；返回实际记录的条数。"""

        items = self._read_decisions()
        decided_at = datetime.now().astimezone().isoformat()
        appended = 0
        for raw in evidence_ids:
            try:
                evidence_id = int(raw)
            except (TypeError, ValueError):
                continue
            entry = self._entries.get(evidence_id)
            items.append(
                {
                    "eid": evidence_id,
                    "url": (url_lookup(evidence_id) if url_lookup else ""),
                    "code": entry.code if entry else "",
                    "message": entry.message if entry else "",
                    "checked_at": entry.checked_at if entry else "",
                    "decision": decision,
                    "decided_at": decided_at,
                }
            )
            appended += 1
        if appended:
            self._write_decisions(items)
        return appended

    def prune(self, keep_eids: set[int]) -> None:
        """删除已不在任务中的记录候选（记录删除后联动清理）。"""

        self.discard(
            {
                evidence_id
                for evidence_id in self._entries
                if evidence_id not in keep_eids
            }
        )

    def discard(self, evidence_ids: Iterable[int]) -> None:
        """清理指定记录的候选（如续跑/重试后已恢复为成功态）。"""

        targets = {int(eid) for eid in evidence_ids}
        remaining = {
            evidence_id: entry
            for evidence_id, entry in self._entries.items()
            if evidence_id not in targets
        }
        if len(remaining) != len(self._entries):
            self._entries = remaining
            self._save()

    def _read_decisions(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self._decisions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, dict):
            return []
        items = raw.get("decisions")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _write_decisions(self, items: list[dict[str, Any]]) -> None:
        try:
            self._decisions_path.write_text(
                json.dumps({"decisions": items}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError as error:
            logger.warning(
                "Unable to persist invalid URL decisions %s: %s",
                self._decisions_path,
                error,
            )


def track_engines(
    base_factory: Callable[[Any], Any],
) -> tuple[list[Any], Callable[[Any], Any]]:
    """包装 engine_factory，收集其创建的全部引擎（含 headed 重试引擎）。"""

    engines: list[Any] = []

    def factory(config: Any) -> Any:
        engine = base_factory(config)
        engines.append(engine)
        return engine

    return engines, factory


def persist_invalid_candidates(
    job_dir: Path,
    candidates: Iterable[Any],
    records: Iterable[RecordResult],
) -> int:
    """把引擎收集的失效候选按 evidence_id 落盘；清理已恢复成功态的陈旧候选。"""

    records = list(records)
    final_status = {record.task.evidence_id: record.status for record in records}
    store = InvalidUrlStore(job_dir)
    written = 0
    for candidate in candidates:
        evidence_id = int(getattr(candidate, "evidence_id"))
        # 候选仅当终态仍是失败/待补录时成立（重试成功的不落盘）。
        if final_status.get(evidence_id) not in _CANDIDATE_STATUSES:
            continue
        store.set(
            evidence_id,
            str(getattr(candidate, "code", "") or ""),
            str(getattr(candidate, "citation", "") or ""),
        )
        written += 1
    store.discard(
        evidence_id
        for evidence_id, status in final_status.items()
        if status not in _CANDIDATE_STATUSES
    )
    return written


def report_invalid_candidates(
    job_dir: Path,
    tracked_engines: Iterable[Any],
    records: Iterable[RecordResult],
    log: Callable[[str, str], None],
) -> int:
    """收集被跟踪引擎（含 headed 重试引擎）的失效候选、落盘并汇总日志。"""

    count = persist_invalid_candidates(
        job_dir,
        (
            candidate
            for tracked in tracked_engines
            for candidate in getattr(tracked, "invalid_candidates", ())
        ),
        records,
    )
    if count:
        log("INFO", f"发现 {count} 条疑似失效链接，任务完成后可在确认弹窗中选择删除。")
    return count


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


__all__ = [
    "DECISION_DELETED",
    "DECISION_KEEP",
    "INVALID_URLS_FILENAME",
    "INVALID_URL_DECISIONS_FILENAME",
    "InvalidUrlStore",
    "persist_invalid_candidates",
    "report_invalid_candidates",
    "track_engines",
]
