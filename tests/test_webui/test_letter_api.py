"""函文档 js_api mixin 离线测试：多选、jpg 白名单校验与状态维护。"""

from __future__ import annotations

from pathlib import Path

from src.webui.letter_api import LetterApiMixin


class _FakeWindow:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[dict] = []

    def create_file_dialog(self, dialog_type: object, **kwargs: object) -> object:
        self.calls.append({"dialog_type": dialog_type, **kwargs})
        return self._result


class _Host(LetterApiMixin):
    def __init__(self, window: _FakeWindow) -> None:
        self._window = window
        self._window_provider = lambda: window
        self._letter_paths: tuple[Path, ...] = ()


def _host_with(result: object) -> _Host:
    return _Host(_FakeWindow(result))


def test_pick_letter_cancel_returns_not_ok() -> None:
    host = _host_with(None)

    result = host.pick_letter_file()

    assert result == {"ok": False, "names": [], "message": ""}
    assert host.letter_state() == {"names": []}


def test_pick_letter_multi_jpg(tmp_path: Path) -> None:
    first = tmp_path / "函1.jpg"
    first.write_bytes(b"1")
    second = tmp_path / "函2.jpeg"
    second.write_bytes(b"2")
    host = _host_with((str(first), str(second)))

    result = host.pick_letter_file()

    assert result["ok"] is True
    assert result["names"] == ["函1.jpg", "函2.jpeg"]
    assert host.letter_state() == {"names": ["函1.jpg", "函2.jpeg"]}
    call = host._window.calls[0]
    assert call["allow_multiple"] is True
    assert ".jpg" in call["file_types"][0]


def test_pick_letter_rejects_non_jpg(tmp_path: Path) -> None:
    image = tmp_path / "函.jpg"
    image.write_bytes(b"1")
    document = tmp_path / "函.docx"
    document.write_bytes(b"d")
    host = _host_with((str(image), str(document)))

    result = host.pick_letter_file()

    assert result["ok"] is False
    assert "函.docx" in result["message"]
    # 校验失败不污染会话状态
    assert host.letter_state() == {"names": []}


def test_pick_letter_dedupes_same_name(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    first = tmp_path / "函.jpg"
    first.write_bytes(b"1")
    second = other / "函.jpg"
    second.write_bytes(b"2")
    host = _host_with((str(first), str(second)))

    result = host.pick_letter_file()

    assert result["names"] == ["函.jpg"]


def test_remove_letter_file_by_name(tmp_path: Path) -> None:
    first = tmp_path / "函1.jpg"
    first.write_bytes(b"1")
    second = tmp_path / "函2.jpg"
    second.write_bytes(b"2")
    host = _host_with((str(first), str(second)))
    host.pick_letter_file()

    result = host.remove_letter_file("函1.jpg")

    assert result == {"ok": True, "names": ["函2.jpg"]}
    assert host.letter_state() == {"names": ["函2.jpg"]}


def test_clear_letter_file_empties_state(tmp_path: Path) -> None:
    first = tmp_path / "函.jpg"
    first.write_bytes(b"1")
    host = _host_with((str(first),))
    host.pick_letter_file()

    host.clear_letter_file()

    assert host.letter_state() == {"names": []}
