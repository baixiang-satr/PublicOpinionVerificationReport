"""Write the fixed template directly as Office Open XML without Excel COM."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from src.domain.models import TemplateRow
from src.domain.template_schema import (
    SHEET_LAYOUTS,
    SHEET_ORDER,
    SheetLayout,
)
from src.export.writer_models import (
    TemplateIntegrityError,
    WorkbookInspection,
    WorkbookWriteResult,
)
from src.utils.file_utils import split_attachment_names

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_SPACE = "http://www.w3.org/XML/1998/namespace"
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")
_INVALID_XML = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]"
)
_DATETIME_FORMAT_CODE = "yyyy-mm-dd hh:mm:ss"

ET.register_namespace("", _MAIN)
ET.register_namespace("r", _DOC_REL)


class OoxmlTemplateWriter:
    """Preserve the template package while replacing only business rows."""

    def __init__(self, workbook_name: str = "template.xlsx") -> None:
        self._workbook_name = workbook_name

    def write(
        self,
        template_dir: Path,
        rows: Iterable[TemplateRow],
    ) -> WorkbookWriteResult:
        template_dir = Path(template_dir).resolve()
        workbook_path = template_dir / self._workbook_name
        grouped = self._group_rows(rows)
        package = _read_package(workbook_path)
        shared_strings = _shared_strings(package)
        sheet_targets = _sheet_targets(package)
        if tuple(sheet_targets) != SHEET_ORDER:
            raise TemplateIntegrityError(
                f"Worksheet order changed: {tuple(sheet_targets)}"
            )
        replacements: dict[str, bytes] = {
            "xl/styles.xml": _rewrite_datetime_number_format(
                package["xl/styles.xml"]
            )
        }
        for sheet_name, target in sheet_targets.items():
            layout = SHEET_LAYOUTS[sheet_name]
            replacements[target] = _rewrite_sheet(
                package[target],
                shared_strings,
                layout,
                grouped.get(sheet_name, []),
            )
        _write_package(workbook_path, package, replacements)
        inspection = self.inspect(workbook_path)
        expected = {
            name
            for sheet_rows in grouped.values()
            for row in sheet_rows
            for name in row.all_asset_names()
        }
        if set(inspection.referenced_assets) != expected:
            raise TemplateIntegrityError(
                "Saved workbook asset references do not match template rows."
            )
        return WorkbookWriteResult(workbook_path, inspection)

    def inspect(self, workbook_path: Path) -> WorkbookInspection:
        package = _read_package(Path(workbook_path).resolve())
        shared_strings = _shared_strings(package)
        sheet_targets = _sheet_targets(package)
        assets: set[str] = set()
        for sheet_name, target in sheet_targets.items():
            layout = SHEET_LAYOUTS[sheet_name]
            root = ET.fromstring(package[target])
            _verify_headers(root, shared_strings, layout)
            for row in _rows(root):
                row_number = int(row.get("r") or 0)
                if row_number < layout.data_start_row:
                    continue
                values = _row_values(row, shared_strings)
                if layout.primary_screenshot_column:
                    screenshot = values.get(
                        layout.primary_screenshot_column,
                        "",
                    ).strip()
                    if screenshot:
                        assets.add(screenshot)
                if layout.attachment_column:
                    assets.update(
                        split_attachment_names(
                            values.get(layout.attachment_column)
                        )
                    )
        return WorkbookInspection(
            tuple(sheet_targets),
            tuple(sorted(assets)),
        )

    def validate_contract(self, workbook_path: Path) -> tuple[str, ...]:
        package = _read_package(Path(workbook_path).resolve())
        shared_strings = _shared_strings(package)
        sheet_targets = _sheet_targets(package)
        actual = tuple(sheet_targets)
        if actual != SHEET_ORDER:
            raise TemplateIntegrityError(
                f"Worksheet order changed: {actual}"
            )
        for sheet_name, target in sheet_targets.items():
            root = ET.fromstring(package[target])
            _verify_headers(
                root,
                shared_strings,
                SHEET_LAYOUTS[sheet_name],
            )
        return actual

    @staticmethod
    def _group_rows(
        rows: Iterable[TemplateRow],
    ) -> dict[str, list[TemplateRow]]:
        grouped: dict[str, list[TemplateRow]] = defaultdict(list)
        for row in rows:
            layout = SHEET_LAYOUTS.get(row.sheet_name)
            if layout is None:
                raise TemplateIntegrityError(
                    f"Unsupported worksheet: {row.sheet_name}"
                )
            unknown = set(row.values_by_column) - {
                _column_name(index)
                for index in range(1, layout.column_count + 1)
            }
            if unknown:
                raise TemplateIntegrityError(
                    f"Unknown columns for {layout.name}: {sorted(unknown)}"
                )
            grouped[row.sheet_name].append(row)
        for sheet_rows in grouped.values():
            sheet_rows.sort(key=lambda row: row.evidence_id)
        return grouped


def _read_package(path: Path) -> dict[str, bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with ZipFile(path) as archive:
            return {
                item.filename: archive.read(item.filename)
                for item in archive.infolist()
            }
    except BadZipFile as error:
        raise TemplateIntegrityError(
            "template.xlsx 不是有效的 Office Open XML 工作簿"
        ) from error


def _write_package(
    path: Path,
    package: dict[str, bytes],
    replacements: dict[str, bytes],
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, replacements.get(name, data))
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _rewrite_datetime_number_format(xml: bytes) -> bytes:
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


def _sheet_targets(package: dict[str, bytes]) -> dict[str, str]:
    workbook = ET.fromstring(package["xl/workbook.xml"])
    relations = ET.fromstring(package["xl/_rels/workbook.xml.rels"])
    relation_targets = {
        relation.get("Id"): relation.get("Target")
        for relation in relations.findall(f"{{{_PKG_REL}}}Relationship")
    }
    targets: dict[str, str] = {}
    sheets = workbook.find(f"{{{_MAIN}}}sheets")
    if sheets is None:
        raise TemplateIntegrityError("Workbook has no worksheets.")
    for sheet in sheets:
        name = str(sheet.get("name") or "")
        relation_id = sheet.get(f"{{{_DOC_REL}}}id")
        target = relation_targets.get(relation_id)
        if not target:
            raise TemplateIntegrityError(
                f"Worksheet relationship is missing: {name}"
            )
        clean_target = target.lstrip("/")
        normalized = (
            clean_target
            if clean_target.startswith("xl/")
            else str(PurePosixPath("xl") / clean_target)
        )
        targets[name] = normalized
    return targets


def _shared_strings(package: dict[str, bytes]) -> tuple[str, ...]:
    raw = package.get("xl/sharedStrings.xml")
    if raw is None:
        return ()
    root = ET.fromstring(raw)
    return tuple(
        "".join(node.text or "" for node in item.iter(f"{{{_MAIN}}}t"))
        for item in root.findall(f"{{{_MAIN}}}si")
    )


def _rewrite_sheet(
    xml: bytes,
    shared_strings: tuple[str, ...],
    layout: SheetLayout,
    rows: list[TemplateRow],
) -> bytes:
    root = ET.fromstring(xml)
    _verify_headers(root, shared_strings, layout)
    sheet_data = root.find(f"{{{_MAIN}}}sheetData")
    if sheet_data is None:
        raise TemplateIntegrityError(
            f"Worksheet has no sheetData: {layout.name}"
        )
    existing_rows = list(sheet_data.findall(f"{{{_MAIN}}}row"))
    prototype = next(
        (
            row
            for row in existing_rows
            if int(row.get("r") or 0) == layout.data_start_row
        ),
        None,
    )
    if prototype is None:
        raise TemplateIntegrityError(
            f"Template input row is missing: {layout.name}"
        )
    for row in existing_rows:
        if int(row.get("r") or 0) >= layout.data_start_row:
            sheet_data.remove(row)
    column_styles = _column_styles(root)
    for offset, template_row in enumerate(rows):
        row_number = layout.data_start_row + offset
        sheet_data.append(
            _build_row(
                prototype,
                row_number,
                layout,
                template_row,
                column_styles,
            )
        )
    dimension = root.find(f"{{{_MAIN}}}dimension")
    if dimension is not None:
        last_row = max(2, layout.data_start_row + len(rows) - 1)
        dimension.set(
            "ref",
            f"A1:{_column_name(layout.column_count)}{last_row}",
        )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _build_row(
    prototype: ET.Element,
    row_number: int,
    layout: SheetLayout,
    template_row: TemplateRow,
    column_styles: dict[str, str],
) -> ET.Element:
    attributes = dict(prototype.attrib)
    attributes["r"] = str(row_number)
    attributes["ht"] = "90"
    attributes["customHeight"] = "1"
    row = ET.Element(f"{{{_MAIN}}}row", attributes)
    prototypes = {
        _cell_column(cell): cell
        for cell in prototype.findall(f"{{{_MAIN}}}c")
    }
    for column_number in range(1, layout.column_count + 1):
        column = _column_name(column_number)
        source = prototypes.get(column)
        cell_attributes = (
            {
                key: value
                for key, value in source.attrib.items()
                if key not in {"r", "t"}
            }
            if source is not None
            else {}
        )
        # Some template cells intentionally omit an explicit ``s`` attribute
        # and inherit their format from ``<col style="…">``. Creating a
        # concrete value cell without restoring that inherited style resets
        # it to General, exposing datetimes as Excel serial numbers.
        if "s" not in cell_attributes and column in column_styles:
            cell_attributes["s"] = column_styles[column]
        cell_attributes["r"] = f"{column}{row_number}"
        cell = ET.SubElement(
            row,
            f"{{{_MAIN}}}c",
            cell_attributes,
        )
        value = template_row.values_by_column.get(column)
        if value is not None and str(value).strip() != "":
            _set_cell_value(cell, value)
    return row


def _column_styles(root: ET.Element) -> dict[str, str]:
    """Expand worksheet column styles to an A1 column-name lookup."""

    styles: dict[str, str] = {}
    columns = root.find(f"{{{_MAIN}}}cols")
    if columns is None:
        return styles
    for item in columns.findall(f"{{{_MAIN}}}col"):
        style = item.get("style")
        if style is None:
            continue
        try:
            first = int(item.get("min") or 0)
            last = int(item.get("max") or 0)
        except ValueError:
            continue
        if first < 1 or last < first:
            continue
        for index in range(first, min(last, 16_384) + 1):
            styles[_column_name(index)] = style
    return styles


def _set_cell_value(cell: ET.Element, value: Any) -> None:
    if isinstance(value, datetime):
        cell.set("t", "n")
        serial = (
            value.replace(tzinfo=None) - datetime(1899, 12, 30)
        ).total_seconds() / 86_400
        ET.SubElement(cell, f"{{{_MAIN}}}v").text = f"{serial:.10f}"
        return
    if isinstance(value, bool):
        cell.set("t", "b")
        ET.SubElement(cell, f"{{{_MAIN}}}v").text = "1" if value else "0"
        return
    if isinstance(value, (int, float)):
        cell.set("t", "n")
        ET.SubElement(cell, f"{{{_MAIN}}}v").text = str(value)
        return
    cell.set("t", "inlineStr")
    inline = ET.SubElement(cell, f"{{{_MAIN}}}is")
    text = ET.SubElement(inline, f"{{{_MAIN}}}t")
    normalized = _INVALID_XML.sub("", str(value))
    if normalized != normalized.strip() or "\n" in normalized:
        text.set(f"{{{_XML_SPACE}}}space", "preserve")
    text.text = normalized


def _verify_headers(
    root: ET.Element,
    shared_strings: tuple[str, ...],
    layout: SheetLayout,
) -> None:
    first_row = next(
        (
            row
            for row in _rows(root)
            if int(row.get("r") or 0) == 1
        ),
        None,
    )
    if first_row is None:
        raise TemplateIntegrityError(
            f"Header row is missing: {layout.name}"
        )
    values = _row_values(first_row, shared_strings)
    actual = tuple(
        values.get(_column_name(index), "")
        for index in range(1, layout.column_count + 1)
    )
    if actual != layout.headers:
        raise TemplateIntegrityError(
            f"Header contract changed: {layout.name}"
        )


def _rows(root: ET.Element) -> list[ET.Element]:
    sheet_data = root.find(f"{{{_MAIN}}}sheetData")
    return (
        list(sheet_data.findall(f"{{{_MAIN}}}row"))
        if sheet_data is not None
        else []
    )


def _row_values(
    row: ET.Element,
    shared_strings: tuple[str, ...],
) -> dict[str, str]:
    return {
        _cell_column(cell): _cell_text(cell, shared_strings)
        for cell in row.findall(f"{{{_MAIN}}}c")
    }


def _cell_text(
    cell: ET.Element,
    shared_strings: tuple[str, ...],
) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(f"{{{_MAIN}}}is")
        return (
            "".join(
                node.text or ""
                for node in inline.iter(f"{{{_MAIN}}}t")
            )
            if inline is not None
            else ""
        )
    raw = cell.findtext(f"{{{_MAIN}}}v", default="")
    if kind == "s" and raw:
        try:
            return shared_strings[int(raw)]
        except (IndexError, ValueError):
            return ""
    return raw


def _cell_column(cell: ET.Element) -> str:
    reference = str(cell.get("r") or "")
    match = _CELL_REF.match(reference)
    return match.group(1) if match else ""


def _column_name(index: int) -> str:
    output = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        output = chr(65 + remainder) + output
    return output
