"""函文档上传接收离线测试：jpg 白名单校验、去重与已选列表维护。"""

from __future__ import annotations

from pathlib import Path

from src.webui.intake_api import FileIntakeApiMixin
from src.webui.letter_api import LetterApiMixin


class _Host(FileIntakeApiMixin, LetterApiMixin):
    def __init__(self) -> None:
        self._letter_paths: tuple[Path, ...] = ()


def _jpg(path: Path) -> Path:
    path.write_bytes(b"\xff\xd8\xff" + b"0" * 16)
    return path


def test_accept_letter_multi_jpg(tmp_path: Path) -> None:
    first = _jpg(tmp_path / "函1.jpg")
    second = _jpg(tmp_path / "函2.jpeg")
    host = _Host()

    result = host.accept_letter_files([str(first), str(second)])

    assert result["ok"] is True
    assert result["names"] == ["函1.jpg", "函2.jpeg"]
    assert host.letter_state() == {"names": ["函1.jpg", "函2.jpeg"]}


def test_accept_letter_rejects_non_jpg(tmp_path: Path) -> None:
    image = _jpg(tmp_path / "函.jpg")
    document = tmp_path / "函.docx"
    document.write_bytes(b"d")
    host = _Host()

    result = host.accept_letter_files([str(image), str(document)])

    assert result["ok"] is False
    assert "函.docx" in result["message"]
    # 校验失败不污染会话状态
    assert host.letter_state() == {"names": []}


def test_accept_letter_dedupes_same_name(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    first = _jpg(tmp_path / "函.jpg")
    second = _jpg(other / "函.jpg")
    host = _Host()

    result = host.accept_letter_files([str(first), str(second)])

    assert result["names"] == ["函.jpg"]


def test_remove_letter_file_by_name(tmp_path: Path) -> None:
    first = _jpg(tmp_path / "函1.jpg")
    second = _jpg(tmp_path / "函2.jpg")
    host = _Host()
    host.accept_letter_files([str(first), str(second)])

    result = host.remove_letter_file("函1.jpg")

    assert result == {"ok": True, "names": ["函2.jpg"]}
    assert host.letter_state() == {"names": ["函2.jpg"]}


def test_clear_letter_file_empties_state(tmp_path: Path) -> None:
    first = _jpg(tmp_path / "函.jpg")
    host = _Host()
    host.accept_letter_files([str(first)])

    host.clear_letter_file()

    assert host.letter_state() == {"names": []}
