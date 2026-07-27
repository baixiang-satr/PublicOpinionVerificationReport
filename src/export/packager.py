"""Create the fixed template.zip archive from a validated staging/template directory."""

from __future__ import annotations

import zipfile
from pathlib import Path

from src.utils.file_utils import atomic_replace


class PackagingError(ValueError):
    """Raised when a staging directory cannot be packaged under the required layout."""


def create_template_archive(template_dir: Path, archive_path: Path, archive_root_name: str = "template") -> Path:
    template_dir = template_dir.resolve()
    archive_path = archive_path.resolve()
    if template_dir.name != archive_root_name:
        raise PackagingError(f"Staging directory must be named {archive_root_name}.")
    if not template_dir.is_dir():
        raise PackagingError(f"Staging directory does not exist: {template_dir}")
    temporary_path = archive_path.with_suffix(archive_path.suffix + ".tmp")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(candidate for candidate in template_dir.rglob("*") if candidate.is_file()):
                arcname = Path(archive_root_name) / path.relative_to(template_dir)
                archive.write(path, arcname.as_posix())
        atomic_replace(temporary_path, archive_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return archive_path
