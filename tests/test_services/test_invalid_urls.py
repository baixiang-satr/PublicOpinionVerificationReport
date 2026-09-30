"""失效候选持久化：invalid_urls.json 候选表 + invalid_url_decisions.json 留痕。"""

from __future__ import annotations

import json
from pathlib import Path

from src.domain.models import PageData, RecordResult, RecordStatus, UrlTask
from src.services.invalid_urls import (
    DECISION_DELETED,
    DECISION_KEEP,
    INVALID_URLS_FILENAME,
    INVALID_URL_DECISIONS_FILENAME,
    InvalidUrlStore,
    persist_invalid_candidates,
    track_engines,
)
from src.crawler.llm_deletion import InvalidUrlCandidate


def _record(evidence_id: int, status: RecordStatus) -> RecordResult:
    url = f"https://a.test/{evidence_id}"
    return RecordResult(
        task=UrlTask(evidence_id, url, url),
        status=status,
        page=PageData(final_url=url),
    )


def test_store_set_pending_reload(tmp_path: Path) -> None:
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_NOT_FOUND", "HTTP 404")
    store.set(2, "CONTENT_DELETED_LLM", "该作品已被作者删除")

    assert (tmp_path / INVALID_URLS_FILENAME).is_file()
    reloaded = InvalidUrlStore(tmp_path)
    assert reloaded.get(1).code == "CONTENT_NOT_FOUND"  # type: ignore[union-attr]
    assert reloaded.get(2).message == "该作品已被作者删除"  # type: ignore[union-attr]
    assert reloaded.get(1).status.value == "invalid"  # type: ignore[union-attr]
    assert set(reloaded.pending()) == {1, 2}


def test_record_decisions_persist_and_filter_pending(tmp_path: Path) -> None:
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_DELETED_LLM", "该作品已被作者删除")
    store.set(2, "CONTENT_UNAVAILABLE", "视频不见了")

    kept = store.record_decisions([1], DECISION_KEEP, lambda eid: f"https://a.test/{eid}")

    assert kept == 1
    reloaded = InvalidUrlStore(tmp_path)
    assert set(reloaded.pending()) == {2}  # 已保留的不再进弹窗
    raw = json.loads((tmp_path / INVALID_URL_DECISIONS_FILENAME).read_text(encoding="utf-8"))
    [item] = raw["decisions"]
    assert item["eid"] == 1
    assert item["decision"] == DECISION_KEEP
    assert item["message"] == "该作品已被作者删除"  # 引文快照留痕
    assert item["checked_at"] and item["decided_at"]  # 判定与决策双时间戳
    assert item["url"] == "https://a.test/1"


def test_prune_removes_candidates_but_keeps_decision_log(tmp_path: Path) -> None:
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_NOT_FOUND", "HTTP 404")
    store.record_decisions([1], DECISION_DELETED, None)

    store.prune(set())  # 记录已删除 → 候选联动清理

    reloaded = InvalidUrlStore(tmp_path)
    assert reloaded.get(1) is None
    assert reloaded.pending() == {}
    # 决策留痕只增不减
    raw = json.loads((tmp_path / INVALID_URL_DECISIONS_FILENAME).read_text(encoding="utf-8"))
    assert [item["decision"] for item in raw["decisions"]] == [DECISION_DELETED]


def test_discard_removes_selected_candidates(tmp_path: Path) -> None:
    store = InvalidUrlStore(tmp_path)
    store.set(1, "CONTENT_NOT_FOUND", "HTTP 404")
    store.set(2, "CONTENT_UNAVAILABLE", "视频不见了")

    store.discard([1])

    reloaded = InvalidUrlStore(tmp_path)
    assert reloaded.get(1) is None
    assert reloaded.get(2) is not None


def test_persist_filters_by_final_record_status(tmp_path: Path) -> None:
    candidates = [
        InvalidUrlCandidate(1, "https://a.test/1", "CONTENT_NOT_FOUND", "HTTP 404"),
        InvalidUrlCandidate(2, "https://a.test/2", "CONTENT_DELETED_LLM", "内容已删除"),
        InvalidUrlCandidate(3, "https://a.test/3", "CONTENT_DELETED_LLM", "内容已删除"),
    ]
    records = [
        _record(1, RecordStatus.FAILED),
        _record(2, RecordStatus.ASSETS_READY),  # 重试成功 → 不落盘
        _record(3, RecordStatus.NEEDS_REVIEW),
    ]

    written = persist_invalid_candidates(tmp_path, candidates, records)

    assert written == 2
    reloaded = InvalidUrlStore(tmp_path)
    assert reloaded.get(1) is not None
    assert reloaded.get(2) is None
    assert reloaded.get(3) is not None


def test_persist_discards_stale_candidates_of_recovered_records(tmp_path: Path) -> None:
    InvalidUrlStore(tmp_path).set(7, "CONTENT_NOT_FOUND", "HTTP 404")

    persist_invalid_candidates(tmp_path, [], [_record(7, RecordStatus.EXPORTED)])

    assert InvalidUrlStore(tmp_path).get(7) is None  # 续跑恢复成功 → 陈旧候选清理


def test_track_engines_collects_all_created_engines() -> None:
    created: list[object] = []

    def base(config: object) -> object:
        engine = object()
        created.append(engine)
        return engine

    engines, factory = track_engines(base)
    first, second = factory(None), factory(None)

    assert engines == [first, second] == created
