"""U01：函文档统一入口——入包、持久化发现与附件列末尾追加。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.models import TemplateRow
from src.domain.template_schema import get_sheet_layout
from src.export import letter_asset
from src.export.letter_asset import (
    LETTER_DIR_NAME,
    append_letter_to_rows,
    find_staged_letter,
    persist_letter_file,
    stage_letter_file,
)

_LETTER = "XX市申请处置的函.docx"


@pytest.fixture()
def letter_file(tmp_path: Path) -> Path:
    path = tmp_path / _LETTER
    path.write_bytes(b"letter-bytes")
    return path


def test_stage_letter_file_copies_into_template_dir(tmp_path: Path, letter_file: Path) -> None:
    template_dir = tmp_path / "template"
    template_dir.mkdir()

    name = stage_letter_file(template_dir, letter_file)

    assert name == _LETTER
    assert (template_dir / _LETTER).read_bytes() == b"letter-bytes"


def test_persist_and_find_staged_letter_roundtrip(tmp_path: Path, letter_file: Path) -> None:
    job_dir = tmp_path / "job"

    persist_letter_file(job_dir, letter_file)

    found = find_staged_letter(job_dir)
    assert found is not None
    assert found.name == _LETTER
    assert found.parent.name == LETTER_DIR_NAME
    # 持久化目录只保留最新一份函
    newer = tmp_path / "另一份函.pdf"
    newer.write_bytes(b"new")
    persist_letter_file(job_dir, newer)
    found = find_staged_letter(job_dir)
    assert found is not None and found.name == "另一份函.pdf"


def test_find_staged_letter_missing_dir(tmp_path: Path) -> None:
    assert find_staged_letter(tmp_path / "no-such-job") is None


def _row(attachments: tuple[str, ...], values: dict[str, object]) -> TemplateRow:
    return TemplateRow("微博博客", 1, values, "001.jpg", attachments)


def test_append_letter_appends_after_existing_attachments() -> None:
    layout = get_sheet_layout("微博博客")
    assert layout.attachment_column
    row = _row(("002主页.jpg",), {layout.attachment_column: "002主页.jpg"})

    (updated,) = append_letter_to_rows([row], _LETTER)

    assert updated.attachment_names == ("002主页.jpg", _LETTER)
    assert updated.values_by_column[layout.attachment_column] == f"002主页.jpg,{_LETTER}"


def test_append_letter_alone_when_no_attachments() -> None:
    layout = get_sheet_layout("微博博客")
    row = _row((), {})

    (updated,) = append_letter_to_rows([row], _LETTER)

    assert updated.attachment_names == (_LETTER,)
    assert updated.values_by_column[layout.attachment_column] == _LETTER


def test_append_letter_dedupes_and_moves_to_end() -> None:
    layout = get_sheet_layout("微博博客")
    row = _row(
        (_LETTER, "002主页.jpg"),
        {layout.attachment_column: f"{_LETTER},002主页.jpg"},
    )

    (updated,) = append_letter_to_rows([row], _LETTER)

    assert updated.attachment_names == ("002主页.jpg", _LETTER)
    assert updated.values_by_column[layout.attachment_column] == f"002主页.jpg,{_LETTER}"


def test_append_letter_never_shadows_primary_screenshot() -> None:
    row = _row((), {})

    (updated,) = append_letter_to_rows([row], "001.jpg")

    # 与主截图同名的函名会被剔除出附件列表，仅保留主截图列引用
    assert updated.attachment_names == ("001.jpg",)
    assert updated.primary_screenshot_name == "001.jpg"


def test_append_letter_skips_sheet_without_attachment_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    layout = get_sheet_layout("微博博客")
    monkeypatch.setattr(
        letter_asset,
        "get_sheet_layout",
        lambda _name: replace(layout, attachment_column=None),
    )
    row = _row((), {})

    (updated,) = append_letter_to_rows([row], _LETTER)

    assert updated is row
