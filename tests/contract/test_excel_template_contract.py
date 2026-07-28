from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from src.config.settings import TemplateConfig
from src.domain.models import AssetSet, PageData, RecordResult, RecordStatus, RouteDecision, UrlTask
from src.domain.template_schema import SHEET_ORDER
from src.export.excel_writer import ExcelAutomationUnavailable, ExcelTemplateWriter
from src.export.packager import create_template_archive
from src.export.package_validator import validate_template_assets
from src.export.template_manager import TemplateManager
from src.utils.file_utils import build_file_manifest


pytestmark = pytest.mark.excel


def test_excel_writer_preserves_template_contract_and_references_assets(tmp_path: Path) -> None:
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 is not installed")

    project_root = Path(__file__).resolve().parents[2]
    source_template = project_root / "template"
    source_manifest = build_file_manifest(source_template)
    writer = ExcelTemplateWriter()
    try:
        assert writer.validate_contract(source_template / "template.xlsx") == SHEET_ORDER
    except ExcelAutomationUnavailable as error:
        pytest.skip(str(error))
    assert build_file_manifest(source_template) == source_manifest

    manager = TemplateManager(TemplateConfig(source_dir=project_root / "template", output_dir=tmp_path / "output"))
    prepared = manager.prepare("excel_contract")
    screenshot = prepared.template_dir / "001.jpg"
    screenshot.write_bytes(b"not-an-embedded-image")
    result = RecordResult(
        task=UrlTask(1, "https://example.com/a", "https://example.com/a"),
        status=RecordStatus.READY_FOR_EXPORT,
        route=RouteDecision("公众号", "微信-公众号", "正文"),
        page=PageData(
            final_url="https://example.com/a",
            title="测试标题",
            content_summary="测试内容",
            author_id="test-account",
            author_name="测试账号",
            published_at=datetime(2026, 7, 14, 18, 48),
        ),
        assets=AssetSet(page_screenshot=screenshot),
    )
    from src.export.row_mapper import TemplateRowMapper

    placeholder_results = [
        RecordResult(
            task=UrlTask(
                evidence_id,
                f"https://item.jd.com/{evidence_id}.html",
                f"https://item.jd.com/{evidence_id}.html",
            ),
            status=RecordStatus.FAILED,
            route=RouteDecision(
                "电商平台",
                "京东_京东商城_电商平台",
                "商家",
            ),
        )
        for evidence_id in range(2, 5)
    ]
    rows = [
        TemplateRowMapper().map(result),
        *(TemplateRowMapper().map(item) for item in placeholder_results),
    ]
    try:
        # Three commerce rows exceed the two writable seed rows in the source
        # template and exercise protected-row expansion.
        write_result = writer.write(prepared.template_dir, rows)
    except ExcelAutomationUnavailable as error:
        pytest.skip(str(error))

    validate_template_assets(prepared.template_dir, set(write_result.inspection.referenced_assets))
    manager.assert_source_unchanged(prepared)
    assert write_result.inspection.referenced_assets == ("001.jpg",)

    archive = create_template_archive(prepared.template_dir, prepared.archive_path)
    with ZipFile(archive) as package:
        assert package.namelist() == ["template/001.jpg", "template/template.xlsx"]
