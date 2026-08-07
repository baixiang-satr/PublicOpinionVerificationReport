"""问题1：导出工作簿「格式锁死、数据区内容可编辑可增删」的保护语义。

数据区单元格引用解锁克隆样式（``<protection locked="0"/>``），表头/
示例行保持锁定；sheetProtection 元素保留且格式/结构操作依旧禁止。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from src.domain.models import TemplateRow
from src.export.ooxml_writer import OoxmlTemplateWriter
from src.export.sheet_protection import (
    normalize_sheet_protection,
    unlocked_style_map,
)

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _write_one_row(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "template" / "template.xlsx"
    workbook = tmp_path / "template.xlsx"
    shutil.copy2(source, workbook)
    row = TemplateRow(
        "微博博客",
        1,
        {
            "A": "https://weibo.com/1",
            "B": "昵称一",
            "C": "新浪_新浪微博_博客贴吧",
            "D": "正文",
            "F": "信息一",
            "G": "001.jpg",
        },
        "001.jpg",
    )
    OoxmlTemplateWriter().write(tmp_path, [row])
    return workbook


def _style_locked(styles: ET.Element, index: int) -> bool:
    cell_xfs = styles.find(f"{{{_MAIN}}}cellXfs")
    assert cell_xfs is not None
    xf = list(cell_xfs)[index]
    protection = xf.find(f"{{{_MAIN}}}protection")
    return protection is None or protection.get("locked") != "0"


def test_data_cells_unlocked_headers_and_examples_stay_locked(tmp_path: Path) -> None:
    workbook = _write_one_row(tmp_path)

    with ZipFile(workbook) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet6.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))

    data_cell = sheet.find(f".//{{{_MAIN}}}c[@r='A3']")
    header_cell = sheet.find(f".//{{{_MAIN}}}c[@r='A1']")
    example_cell = sheet.find(f".//{{{_MAIN}}}c[@r='A2']")
    assert data_cell is not None and data_cell.get("s")
    assert header_cell is not None and header_cell.get("s")
    assert not _style_locked(styles, int(data_cell.get("s") or 0))
    assert _style_locked(styles, int(header_cell.get("s") or 0))
    if example_cell is not None and example_cell.get("s"):
        assert _style_locked(styles, int(example_cell.get("s") or 0))


def test_sheet_protection_keeps_format_and_structure_blocked(tmp_path: Path) -> None:
    workbook = _write_one_row(tmp_path)

    with ZipFile(workbook) as archive:
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/sheet"):
                continue
            sheet = ET.fromstring(archive.read(name))
            protection = sheet.find(f"{{{_MAIN}}}sheetProtection")
            assert protection is not None, name
            assert protection.get("sheet") == "1"
            # 格式与行列结构仍禁止修改；选中不受限。
            assert protection.get("formatCells") != "0"
            assert protection.get("formatColumns") != "0"
            assert protection.get("insertColumns") != "0"
            assert protection.get("deleteColumns") != "0"
            assert protection.get("insertRows") != "0"
            assert protection.get("deleteRows") != "0"
            assert protection.get("selectLockedCells") is None
            assert protection.get("selectUnlockedCells") is None


def test_unlocked_clone_preserves_number_format_and_alignment(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "template" / "template.xlsx"
    with ZipFile(source) as archive:
        original = archive.read("xl/styles.xml")
    rewritten, mapping = unlocked_style_map(original)

    assert mapping  # 模板 36 个 xf → 36 个解锁克隆
    original_root = ET.fromstring(original)
    rewritten_root = ET.fromstring(rewritten)
    original_cell_xfs = original_root.find(f"{{{_MAIN}}}cellXfs")
    rewritten_cell_xfs = rewritten_root.find(f"{{{_MAIN}}}cellXfs")
    assert original_cell_xfs is not None and rewritten_cell_xfs is not None
    original_xfs = list(original_cell_xfs)
    rewritten_xfs = list(rewritten_cell_xfs)
    assert len(rewritten_xfs) == len(original_xfs) * 2
    clone = rewritten_xfs[int(mapping["17"])]
    source_xf = original_xfs[17]
    assert clone.get("numFmtId") == source_xf.get("numFmtId")
    assert clone.get("applyProtection") == "1"
    assert clone.find(f"{{{_MAIN}}}alignment") is not None
    protection = clone.find(f"{{{_MAIN}}}protection")
    assert protection is not None and protection.get("locked") == "0"


def test_normalize_sheet_protection_recreates_or_repairs_attributes() -> None:
    sheet = ET.fromstring(
        f'<worksheet xmlns="{_MAIN}"><sheetData/></worksheet>'
    )
    normalize_sheet_protection(sheet)
    protection = sheet.find(f"{{{_MAIN}}}sheetProtection")
    assert protection is not None
    assert protection.get("sheet") == "1"
    # schema 顺序：sheetData 之后
    children = [child.tag for child in sheet]
    assert children.index(f"{{{_MAIN}}}sheetProtection") == children.index(
        f"{{{_MAIN}}}sheetData"
    ) + 1

    broken = ET.fromstring(
        f'<worksheet xmlns="{_MAIN}"><sheetData/>'
        f'<sheetProtection sheet="1" selectLockedCells="1" formatCells="0"'
        f' deleteRows="0" password="ABCD"/></worksheet>'
    )
    normalize_sheet_protection(broken)
    repaired = broken.find(f"{{{_MAIN}}}sheetProtection")
    assert repaired is not None
    assert repaired.get("selectLockedCells") is None
    assert repaired.get("formatCells") is None  # 显式放开被撤销（默认禁止）
    assert repaired.get("deleteRows") is None
    assert repaired.get("password") == "ABCD"  # 密码哈希保留
