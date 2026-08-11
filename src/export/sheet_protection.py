"""数据区解锁样式与工作表保护语义（OOXML 层）。

交付工作簿的每张工作表都带 sheetProtection：格式与列结构锁定，
数据行允许插入/删除（insertRows/deleteRows 显式写 0 放开）。模板的
数据区单元格原本也是锁定样式，收件人无法在 Excel/WPS 里补录内容。
这里在写入业务行前为 cellXfs 的每个 xf 生成「解锁克隆」
（``applyProtection="1"`` + ``<protection locked="0"/>``），写出的数据
单元格全部引用解锁克隆；表头行/示例行不被重写，保持原锁定样式不变。
"""

from __future__ import annotations

from copy import deepcopy
from xml.etree import ElementTree as ET

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

ET.register_namespace("", _MAIN)

# sheetProtection 中「1=禁止」的操作属性：显式写 0 会放开格式/结构修改。
# 数据行增删（insertRows/deleteRows）自 2026-08-11 起放开，不在此列。
_BLOCKED_ACTION_ATTRIBUTES = (
    "formatCells",
    "formatColumns",
    "formatRows",
    "insertColumns",
    "insertHyperlinks",
    "deleteColumns",
    "sort",
    "autoFilter",
    "pivotTables",
)
# 显式写 0 才放开的操作（OOXML 语义：属性缺省=禁止）。
_ALLOWED_ACTION_ATTRIBUTES = ("insertRows", "deleteRows")
# 「1=禁止选中」的选择属性：移除即恢复默认（允许自由选中）。
_SELECTION_ATTRIBUTES = ("selectLockedCells", "selectUnlockedCells")

_DATETIME_FORMAT_CODE = "yyyy-mm-dd hh:mm:ss"


def rewrite_datetime_number_format(xml: bytes) -> bytes:
    """Match the workbook's date display to its yyyy-mm-dd header contract."""

    root = ET.fromstring(xml)
    number_formats = root.find(f"{{{_MAIN}}}numFmts")
    if number_formats is None:
        return xml
    changed = False
    for item in number_formats.findall(f"{{{_MAIN}}}numFmt"):
        code = str(item.get("formatCode") or "").casefold()
        if (
            "yyyy" in code
            and "mm" in code
            and "dd" in code
            and "hh" in code
            and "ss" in code
        ):
            item.set("formatCode", _DATETIME_FORMAT_CODE)
            changed = True
    return (
        ET.tostring(root, encoding="utf-8", xml_declaration=True)
        if changed
        else xml
    )


def rewrite_styles(styles_xml: bytes) -> tuple[bytes, dict[str, str]]:
    """styles.xml 统一重写入口：日期格式契约 + 数据区解锁样式映射。"""

    return unlocked_style_map(rewrite_datetime_number_format(styles_xml))


def unlocked_style_map(styles_xml: bytes) -> tuple[bytes, dict[str, str]]:
    """为 cellXfs 每个 xf 生成解锁克隆。

    返回 ``(新 styles.xml, 旧索引→解锁索引)``。克隆保留原 numFmt/font/
    fill/border/alignment，仅追加 ``<protection locked="0"/>`` 并声明
    ``applyProtection="1"``，因此数字格式（如日期时间）展示不受影响。
    """

    root = ET.fromstring(styles_xml)
    cell_xfs = root.find(f"{{{_MAIN}}}cellXfs")
    if cell_xfs is None:
        return styles_xml, {}
    originals = list(cell_xfs)
    base = len(originals)
    mapping: dict[str, str] = {}
    for offset, xf in enumerate(originals):
        clone = ET.Element(f"{{{_MAIN}}}xf", dict(xf.attrib))
        clone.set("applyProtection", "1")
        protection: ET.Element | None = None
        for child in xf:
            copied = deepcopy(child)
            if copied.tag == f"{{{_MAIN}}}protection":
                protection = copied
            clone.append(copied)
        if protection is None:
            # schema 子元素顺序 alignment → protection → extLst；
            # 模板 xf 无 extLst，直接追加即合法。
            protection = ET.SubElement(clone, f"{{{_MAIN}}}protection")
        protection.set("locked", "0")
        cell_xfs.append(clone)
        mapping[str(offset)] = str(base + offset)
    cell_xfs.set("count", str(base * 2))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), mapping


def normalize_sheet_protection(sheet_root: ET.Element) -> None:
    """把工作表保护规范为「格式/列结构锁定，数据行可增删、内容可编辑」。

    保护元素本身保留（含密码哈希等全部既有属性），只撤销会放开格式/
    结构的显式允许（属性值 0），显式放开数据行增删（insertRows/
    deleteRows=0，OOXML 缺省即禁止，必须写 0 才放开），并移除禁止选中
    的属性以恢复默认自由选中。模板当前属性已符合该语义，本函数是防御性
    兜底。
    """

    protection = sheet_root.find(f"{{{_MAIN}}}sheetProtection")
    if protection is None:
        protection = ET.Element(f"{{{_MAIN}}}sheetProtection")
        protection.set("sheet", "1")
        protection.set("objects", "1")
        protection.set("scenarios", "1")
        for name in _ALLOWED_ACTION_ATTRIBUTES:
            protection.set(name, "0")
        anchor = sheet_root.find(f"{{{_MAIN}}}sheetData")
        if anchor is None:
            sheet_root.append(protection)
        else:
            # schema 顺序：sheetData 之后、protectedRanges/autoFilter 之前。
            sheet_root.insert(list(sheet_root).index(anchor) + 1, protection)
        return
    protection.set("sheet", "1")
    for name in _SELECTION_ATTRIBUTES:
        protection.attrib.pop(name, None)
    for name in _BLOCKED_ACTION_ATTRIBUTES:
        if protection.get(name) == "0":
            del protection.attrib[name]
    for name in _ALLOWED_ACTION_ATTRIBUTES:
        protection.set(name, "0")
