"""U04：URL 复验三态分类与 url_recheck.json 持久化。"""

from __future__ import annotations

from pathlib import Path

from src.domain.models import RecordStatus
from src.services.url_recheck import (
    RECHECK_FILENAME,
    RecheckStatus,
    UrlRecheckStore,
    classify_barrier,
)
from src.tools.page_access import AccessBarrier, AccessKind


def _barrier(kind: AccessKind) -> AccessBarrier:
    return AccessBarrier(kind, "CODE", "消息", RecordStatus.NEEDS_REVIEW)


def test_classify_barrier_three_way_mapping() -> None:
    assert classify_barrier(None) is RecheckStatus.VALID
    assert classify_barrier(_barrier(AccessKind.CONTENT_UNAVAILABLE)) is RecheckStatus.INVALID
    assert classify_barrier(_barrier(AccessKind.REDIRECTED_HOME)) is RecheckStatus.INVALID
    for kind in (
        AccessKind.LOGIN,
        AccessKind.CAPTCHA,
        AccessKind.ACCESS_RESTRICTED,
        AccessKind.JAVASCRIPT_REQUIRED,
        AccessKind.API_RESPONSE,
        AccessKind.EMPTY_RENDERED_PAGE,
    ):
        assert classify_barrier(_barrier(kind)) is RecheckStatus.UNCERTAIN, kind


def test_http_404_maps_to_invalid() -> None:
    from src.tools.page_access import inspect_http_response

    barrier = inspect_http_response(404)
    assert barrier is not None
    assert classify_barrier(barrier) is RecheckStatus.INVALID
    # 风控/权限类状态码只能存疑，不能误删
    barrier403 = inspect_http_response(403)
    assert barrier403 is not None
    assert classify_barrier(barrier403) is RecheckStatus.UNCERTAIN


def test_store_set_get_persist_reload(tmp_path: Path) -> None:
    store = UrlRecheckStore(tmp_path)
    store.set(1, RecheckStatus.INVALID, "CONTENT_NOT_FOUND", "404")
    store.set(2, RecheckStatus.VALID)

    assert (tmp_path / RECHECK_FILENAME).is_file()
    reloaded = UrlRecheckStore(tmp_path)
    assert reloaded.get(1) is not None
    assert reloaded.get(1).status is RecheckStatus.INVALID  # type: ignore[union-attr]
    assert reloaded.get(1).code == "CONTENT_NOT_FOUND"  # type: ignore[union-attr]
    assert reloaded.get(2).status is RecheckStatus.VALID  # type: ignore[union-attr]
    assert reloaded.get(3) is None


def test_store_prune_removes_deleted_records(tmp_path: Path) -> None:
    store = UrlRecheckStore(tmp_path)
    store.set(1, RecheckStatus.INVALID)
    store.set(2, RecheckStatus.VALID)

    store.prune({2})

    reloaded = UrlRecheckStore(tmp_path)
    assert reloaded.get(1) is None
    assert reloaded.get(2) is not None


def test_store_tolerates_missing_or_corrupt_file(tmp_path: Path) -> None:
    assert UrlRecheckStore(tmp_path).get(1) is None
    (tmp_path / RECHECK_FILENAME).write_text("not json", encoding="utf-8")
    assert UrlRecheckStore(tmp_path).get(1) is None
    (tmp_path / RECHECK_FILENAME).write_text("[1,2]", encoding="utf-8")
    assert UrlRecheckStore(tmp_path).get(1) is None
