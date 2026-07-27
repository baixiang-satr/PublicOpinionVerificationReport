from pathlib import Path

from src.config.settings import TemplateConfig
from src.export.template_manager import TemplateManager
from src.utils.file_utils import build_file_manifest


def test_prepare_copies_workbook_and_removes_historical_assets(tmp_path: Path) -> None:
    source = tmp_path / "source-template"
    source.mkdir()
    (source / "template.xlsx").write_bytes(b"fixed workbook")
    (source / "001.jpg").write_bytes(b"historical screenshot")
    (source / "~$template.xlsx").write_bytes(b"Excel lock file")
    source_manifest = build_file_manifest(source)
    manager = TemplateManager(TemplateConfig(source_dir=source, output_dir=tmp_path / "output"))

    prepared = manager.prepare("job_001")

    assert (prepared.template_dir / "template.xlsx").is_file()
    assert not (prepared.template_dir / "001.jpg").exists()
    assert not (prepared.template_dir / "~$template.xlsx").exists()
    assert build_file_manifest(source) == source_manifest
    manager.assert_source_unchanged(prepared)
