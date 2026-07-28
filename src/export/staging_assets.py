"""Keep only files referenced by rows in a prepared template directory."""

from pathlib import Path

from src.domain.models import TemplateRow


def cleanup_staging_assets(
    template_dir: Path,
    rows: list[TemplateRow],
    workbook_name: str,
) -> None:
    expected = {
        name
        for row in rows
        for name in row.all_asset_names()
    }
    for path in list(template_dir.rglob("*")):
        if path.is_dir():
            continue
        if path.name == workbook_name and path.parent == template_dir:
            continue
        if path.parent == template_dir and path.name not in expected:
            path.unlink()
