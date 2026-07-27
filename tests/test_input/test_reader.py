from pathlib import Path

import pytest

from src.input.reader import list_xlsx_sheets, read_url_input


def test_read_url_input_supports_text_and_csv(tmp_path: Path) -> None:
    text_path = tmp_path / "links.txt"
    text_path.write_bytes("https://example.com/a\nhttps://example.com/a\n".encode("utf-8"))
    csv_path = tmp_path / "links.csv"
    csv_path.write_text("name,url\none,https://example.com/b\n", encoding="utf-8")

    text_result = read_url_input(text_path)
    csv_result = read_url_input(csv_path)

    assert [task.normalized_url for task in text_result.tasks] == ["https://example.com/a"]
    assert text_result.duplicate_or_invalid_count == 1
    assert [task.normalized_url for task in csv_result.tasks] == ["https://example.com/b"]


def test_read_url_input_supports_standard_xlsx_and_selected_sheet(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    first_sheet = workbook.active
    first_sheet.title = "Skip"
    first_sheet.append(["https://example.com/ignored"])
    selected_sheet = workbook.create_sheet("Links")
    selected_sheet.append(["https://example.com/a", "https://example.com/a"])
    selected_sheet.append(["https://example.com/b?z=2&y=1"])
    input_path = tmp_path / "links.xlsx"
    workbook.save(input_path)
    workbook.close()

    result = read_url_input(input_path, sheet_name="Links")

    assert list_xlsx_sheets(input_path) == ["Skip", "Links"]
    assert [task.evidence_id for task in result.tasks] == [1, 2]
    assert [task.normalized_url for task in result.tasks] == [
        "https://example.com/a",
        "https://example.com/b?z=2&y=1",
    ]
    assert result.duplicate_or_invalid_count == 1
