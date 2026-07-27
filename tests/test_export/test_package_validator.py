import zipfile
from pathlib import Path

from src.export.package_validator import validate_template_assets
from src.export.packager import create_template_archive


def test_validate_assets_and_create_fixed_archive(tmp_path: Path) -> None:
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "template.xlsx").write_bytes(b"workbook")
    (template_dir / "001.jpg").write_bytes(b"image")

    validation = validate_template_assets(template_dir, {"001.jpg"})
    archive = create_template_archive(template_dir, tmp_path / "template.zip")

    assert validation.referenced_assets == ("001.jpg",)
    with zipfile.ZipFile(archive) as package:
        assert package.namelist() == ["template/001.jpg", "template/template.xlsx"]
