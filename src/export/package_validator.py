"""Validate that fixed-template asset references exactly match staging files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.utils.file_utils import require_safe_file_name


class PackageValidationError(ValueError):
    """Raised when a workbook reference and staging directory do not agree."""


@dataclass(frozen=True)
class AssetValidation:
    referenced_assets: tuple[str, ...]
    existing_assets: tuple[str, ...]


def validate_template_assets(template_dir: Path, referenced_assets: set[str], workbook_name: str = "template.xlsx") -> AssetValidation:
    """Ensure all staging assets are flat, safe, referenced and present."""

    template_dir = template_dir.resolve()
    if not (template_dir / workbook_name).is_file():
        raise PackageValidationError(f"Missing fixed workbook: {workbook_name}")
    required = {require_safe_file_name(name) for name in referenced_assets}
    existing: set[str] = set()
    for path in template_dir.rglob("*"):
        if path.is_dir():
            continue
        if path.name == workbook_name and path.parent == template_dir:
            continue
        if path.parent != template_dir:
            raise PackageValidationError(f"Assets must be flat under template/: {path.relative_to(template_dir)}")
        existing.add(require_safe_file_name(path.name))
    missing = sorted(required - existing)
    unexpected = sorted(existing - required)
    if missing:
        raise PackageValidationError(f"Workbook references missing assets: {', '.join(missing)}")
    if unexpected:
        raise PackageValidationError(f"Staging contains unreferenced assets: {', '.join(unexpected)}")
    return AssetValidation(tuple(sorted(required)), tuple(sorted(existing)))
