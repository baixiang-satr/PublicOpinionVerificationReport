from pathlib import Path
import shutil
from zipfile import ZipFile

from src.domain.models import TemplateRow
from src.domain.template_schema import SHEET_ORDER
from src.export.ooxml_writer import OoxmlTemplateWriter


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
