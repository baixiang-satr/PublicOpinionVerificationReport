"""失效候选确认弹窗 js_api：列表/保留留痕/删除联动清理（全离线假会话）。"""

from __future__ import annotations

import json
from pathlib import Path

from src.domain.models import PageData, RecordResult, RecordStatus, UrlTask
from src.services.invalid_urls import (
    DECISION_DELETED,
    INVALID_URL_DECISIONS_FILENAME,
    InvalidUrlStore,
)
from src.webui.invalid_url_api import InvalidUrlApiMixin
from src.webui.recheck_api import RecheckApiMixin
from src.webui.runner import EventSink


def _record(evidence_id: int, url: str) -> RecordResult:
    return RecordResult(
        task=UrlTask(evidence_id, url, url),
        status=RecordStatus.FAILED,
        page=PageData(final_url=url),
    )


class _FakeSession:
    def __init__(self, job_dir: Path, records: list[RecordResult]) -> None:
        self.job_dir = job_dir
        self._records = records
        self.removed: list[int] = []

    def records(self) -> list[RecordResult]:
        return list(self._records)

    def evidence_ids(self) -> list[int]:
        return [record.task.evidence_id for record in self._records]

    def remove_record(self, evidence_id: int) -> bool:
        remaining = [
            record for record in self._records if record.task.evidence_id != evidence_id
        ]
        if len(remaining) == len(self._records):
            return False
        self.removed.append(evidence_id)
        self._records = remaining
        return True


class _Host(InvalidUrlApiMixin, RecheckApiMixin):
    def __init__(self, session: _FakeSession | None) -> None:
        self._session_obj = session
        self._sink = EventSink()

    def _session(self) -> _FakeSession | None:
        return self._session_obj


def _decision_log(job_dir: Path) -> list[dict]:
    raw = json.loads((job_dir / INVALID_URL_DECISIONS_FILENAME).read_text(encoding="utf-8"))
    return raw["decisions"]


def test_list_candidates_joins_pending_store_with_session(tmp_path: Path) -> None:
    records = [_record(1, "https://a.test/1"), _record(2, "https://a.test/2")]
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_NOT_FOUND", "HTTP 404")
    store.set(2, "CONTENT_DELETED_LLM", "该作品已被作者删除")
    store.set(99, "CONTENT_NOT_FOUND", "HTTP 404")  # 不在会话中的陈旧条目
    store.record_decisions([2], "keep", None)  # 已有决策的不再进弹窗
    host = _Host(_FakeSession(tmp_path, records))

    payload = host.list_invalid_url_candidates()

    assert payload["ok"] is True
    assert [row["eid"] for row in payload["rows"]] == [1]
    row = payload["rows"][0]
    assert row["url"] == "https://a.test/1"
    assert row["code"] == "CONTENT_NOT_FOUND"
    assert row["message"] == "HTTP 404"
    assert row["checked_at"]


def test_list_candidates_without_session(tmp_path: Path) -> None:
    host = _Host(None)
    assert host.list_invalid_url_candidates() == {"ok": False, "rows": []}
    assert host.keep_invalid_url_candidates([1]) == {"ok": False, "kept": 0}


def test_keep_candidates_records_decision_and_clears_pending(tmp_path: Path) -> None:
    records = [_record(1, "https://a.test/1"), _record(2, "https://a.test/2")]
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_DELETED_LLM", "该作品已被作者删除")
    store.set(2, "CONTENT_UNAVAILABLE", "视频不见了")
    host = _Host(_FakeSession(tmp_path, records))

    result = host.keep_invalid_url_candidates([1, "bad"])

    assert result == {"ok": True, "kept": 1}
    reloaded = InvalidUrlStore(tmp_path)
    assert set(reloaded.pending()) == {2}
    [item] = _decision_log(tmp_path)
    assert item["eid"] == 1
    assert item["decision"] == "keep"
    assert item["message"] == "该作品已被作者删除"
    assert item["url"] == "https://a.test/1"


def test_remove_records_prunes_candidates_and_logs_deleted(tmp_path: Path) -> None:
    records = [_record(1, "https://a.test/1"), _record(2, "https://a.test/2")]
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_DELETED_LLM", "该作品已被作者删除")
    store.set(2, "CONTENT_UNAVAILABLE", "视频不见了")
    host = _Host(_FakeSession(tmp_path, records))

    result = host.remove_records([1, 999])

    assert result == {"ok": True, "removed": 1}
    assert host._session_obj.removed == [1]  # 资产清理走 session.remove_record
    reloaded = InvalidUrlStore(tmp_path)
    assert reloaded.get(1) is None  # 候选联动清理
    assert reloaded.get(2) is not None
    [item] = _decision_log(tmp_path)
    assert item["eid"] == 1
    assert item["decision"] == DECISION_DELETED
    assert item["message"] == "该作品已被作者删除"  # 引文快照留痕
    assert item["url"] == "https://a.test/1"
    assert item["checked_at"] and item["decided_at"]
