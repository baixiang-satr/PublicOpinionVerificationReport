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


def test_export_manual_entries_copies_csv(tmp_path: Path) -> None:
    source = _write_csv(
        tmp_path,
        [{"证据编号": "—", "原始URL": "bad-token", "状态": "input_rejected"}],
    )
    target = tmp_path / "out" / "清单.csv"
    host = _Host(_Jobs(_Session(tmp_path)), _FakeWindow(str(target)))

    result = host.export_manual_entries()

    assert result["ok"] is True
    assert target.read_bytes() == source.read_bytes()


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
