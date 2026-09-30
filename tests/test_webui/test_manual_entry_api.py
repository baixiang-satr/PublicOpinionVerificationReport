"""未收录/待补录清单 js_api mixin 离线测试：CSV 解析与导出复制。"""

from __future__ import annotations

import csv
from pathlib import Path

from src.webui.manual_entry_api import MANUAL_ENTRY_FILE_NAME, ManualEntryApiMixin


class _FakeWindow:
    def __init__(self, result: object) -> None:
        self._result = result

    def create_file_dialog(self, dialog_type: object, **kwargs: object) -> object:
        return self._result


class _Session:
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir


class _LiveSession(_Session):
    """带记录/缺失判定能力的假会话（duck-typing 满足 _filter_completed）。"""

    def __init__(self, job_dir: Path, missing_map: dict[int, tuple[str, ...]]) -> None:
        super().__init__(job_dir)
        self._missing_map = missing_map

    def get_record(self, evidence_id: int) -> int:
        if evidence_id not in self._missing_map:
            raise KeyError(evidence_id)
        return evidence_id

    def get_override(self, evidence_id: int) -> None:
        return None

    def missing_labels(self, record: int, override: object) -> tuple[str, ...]:
        return self._missing_map[record]


class _Jobs:
    def __init__(self, session: _Session | None) -> None:
        self.session = session


class _Host(ManualEntryApiMixin):
    def __init__(self, jobs: _Jobs, window: _FakeWindow) -> None:
        self.jobs = jobs
        self._window_provider = lambda: window


def _write_csv(job_dir: Path, rows: list[dict[str, str]]) -> Path:
    path = job_dir / MANUAL_ENTRY_FILE_NAME
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("证据编号", "原始URL", "状态"))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_list_manual_entries_without_session() -> None:
    host = _Host(_Jobs(None), _FakeWindow(None))

    result = host.list_manual_entries()

    assert result["ok"] is False
    assert result["rows"] == []


def test_list_manual_entries_missing_file(tmp_path: Path) -> None:
    host = _Host(_Jobs(_Session(tmp_path)), _FakeWindow(None))

    assert host.list_manual_entries()["ok"] is False


def test_list_manual_entries_reads_csv(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        [{"证据编号": "001", "原始URL": "https://a.test/", "状态": "needs_review"}],
    )
    host = _Host(_Jobs(_Session(tmp_path)), _FakeWindow(None))

    result = host.list_manual_entries()

    assert result["ok"] is True
    assert result["rows"][0]["原始URL"] == "https://a.test/"
    assert result["path"].endswith(MANUAL_ENTRY_FILE_NAME)


def test_list_manual_entries_hides_completed_rows(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        [
            {"证据编号": "001", "原始URL": "https://a.test/", "状态": "needs_review"},
            {"证据编号": "002", "原始URL": "https://b.test/", "状态": "needs_review"},
            {"证据编号": "—", "原始URL": "bad-token", "状态": "input_rejected"},
        ],
    )
    session = _LiveSession(tmp_path, {1: (), 2: ("信息内容",)})
    host = _Host(_Jobs(session), _FakeWindow(None))

    result = host.list_manual_entries()

    assert result["ok"] is True
    assert result["completed_count"] == 1
    urls = [row["原始URL"] for row in result["rows"]]
    assert urls == ["https://b.test/", "bad-token"]  # 已完成隐藏，被拒行保留


def test_list_manual_entries_hides_deleted_records(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        [{"证据编号": "003", "原始URL": "https://c.test/", "状态": "needs_review"}],
    )
    host = _Host(_Jobs(_LiveSession(tmp_path, {})), _FakeWindow(None))  # 记录已删除

    result = host.list_manual_entries()

    assert result["rows"] == []
    assert result["completed_count"] == 1


def test_export_manual_entries_writes_filtered_csv(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        [
            {"证据编号": "001", "原始URL": "https://a.test/", "状态": "needs_review"},
            {"证据编号": "—", "原始URL": "bad-token", "状态": "input_rejected"},
        ],
    )
    target = tmp_path / "out" / "清单.csv"
    host = _Host(_Jobs(_LiveSession(tmp_path, {1: ()})), _FakeWindow(str(target)))

    result = host.export_manual_entries()

    assert result["ok"] is True
    assert "已补录完成" in result["message"]
    with target.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["原始URL"] for row in rows] == ["bad-token"]


def test_export_manual_entries_copies_csv(tmp_path: Path) -> None:
    source = _write_csv(
        tmp_path,
        [{"证据编号": "—", "原始URL": "bad-token", "状态": "input_rejected"}],
    )
    target = tmp_path / "out" / "清单.csv"
    host = _Host(_Jobs(_Session(tmp_path)), _FakeWindow(str(target)))

    result = host.export_manual_entries()

    assert result["ok"] is True
    with source.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    with target.open(encoding="utf-8-sig", newline="") as stream:
        target_rows = list(csv.DictReader(stream))
    assert target_rows == source_rows


def test_export_manual_entries_appends_csv_suffix(tmp_path: Path) -> None:
    _write_csv(tmp_path, [])
    host = _Host(_Jobs(_Session(tmp_path)), _FakeWindow(str(tmp_path / "no-suffix")))

    result = host.export_manual_entries()

    assert result["ok"] is True
    assert (tmp_path / "no-suffix.csv").is_file()


def test_export_manual_entries_cancel_returns_not_ok(tmp_path: Path) -> None:
    _write_csv(tmp_path, [])
    host = _Host(_Jobs(_Session(tmp_path)), _FakeWindow(None))

    assert host.export_manual_entries() == {"ok": False, "message": ""}
