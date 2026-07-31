import shutil
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from src.domain.models import TemplateRow
from src.domain.template_schema import SHEET_ORDER
from src.export.ooxml_writer import OoxmlTemplateWriter
from src.utils.time_utils import DEFAULT_TIMEZONE

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def test_ooxml_writer_preserves_contract_and_writes_without_excel(
    tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[2] / "template" / "template.xlsx"
    workbook = tmp_path / "template.xlsx"
    shutil.copy2(source, workbook)
    rows = [
        TemplateRow(
            "微博博客",
            2,
            {
                "A": "https://weibo.com/2",
                "B": "昵称二",
                "C": "新浪_新浪微博_博客贴吧",
                "D": "正文",
                "F": "信息二",
                "G": "002.jpg",
            },
            "002.jpg",
        ),
        TemplateRow(
            "微博博客",
            1,
            {
                "A": "https://weibo.com/1",
                "B": "昵称一",
                "C": "新浪_新浪微博_博客贴吧",
                "D": "正文",
                "F": "信息一",
                "G": "001.jpg",
                "H": "001主页.jpg",
            },
            "001.jpg",
            ("001主页.jpg",),
        ),
    ]

    result = OoxmlTemplateWriter().write(tmp_path, rows)

    assert result.workbook_path.read_bytes()[:2] == b"PK"
    assert result.inspection.sheet_names == SHEET_ORDER
    assert result.inspection.referenced_assets == (
        "001.jpg",
        "001主页.jpg",
        "002.jpg",
    )
    assert OoxmlTemplateWriter().validate_contract(workbook) == SHEET_ORDER
    with ZipFile(workbook) as archive:
        worksheet_xml = b"".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet")
        )
    assert b'ht="90"' in worksheet_xml
    assert b'customHeight="1"' in worksheet_xml


def test_ooxml_writer_preserves_inherited_datetime_column_style(
    tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[2] / "template" / "template.xlsx"
    workbook = tmp_path / "template.xlsx"
    shutil.copy2(source, workbook)
    row = TemplateRow(
        "图文视频",
        1,
        {
            "A": "https://www.xiaohongshu.com/explore/abc123",
            "B": "535526151",
            "C": "测试作者",
            "D": "行吟科技_小红书_图文视频",
            "E": "正文",
            "F": datetime(
                2026,
                7,
                20,
                15,
                55,
                6,
                tzinfo=DEFAULT_TIMEZONE,
            ),
            "G": "测试正文",
        },
    )

    OoxmlTemplateWriter().write(tmp_path, [row])

    with ZipFile(workbook) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet5.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
    cell = sheet.find(f".//{{{_MAIN}}}c[@r='F3']")
    assert cell is not None
    style_index = int(cell.get("s") or -1)
    cell_xfs = styles.find(f"{{{_MAIN}}}cellXfs")
    assert cell_xfs is not None
    style = list(cell_xfs)[style_index]
    number_format_id = style.get("numFmtId")
    number_formats = styles.find(f"{{{_MAIN}}}numFmts")
    assert number_formats is not None
    number_format = number_formats.find(
        f"{{{_MAIN}}}numFmt[@numFmtId='{number_format_id}']"
    )
    assert number_format is not None
    assert number_format.get("formatCode") == "yyyy-mm-dd hh:mm:ss"
