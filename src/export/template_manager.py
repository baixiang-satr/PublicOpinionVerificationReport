"""Create isolated template staging directories without modifying the source template."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import TemplateConfig
from src.utils.file_utils import FileDigest, assert_manifest_unchanged, build_file_manifest


JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


@dataclass(frozen=True)
class PreparedTemplate:
    job_id: str
    job_dir: Path
    staging_dir: Path
    template_dir: Path
    archive_path: Path
    source_manifest: dict[str, FileDigest]


class TemplateManager:
    """Own staging output while treating the configured template directory as read-only."""

    def __init__(self, config: TemplateConfig) -> None:
        self._config = config

    def prepare(self, job_id: str) -> PreparedTemplate:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("job_id may only contain letters, digits, underscores and hyphens.")
        source_dir = self._config.source_dir.resolve()
        workbook = source_dir / self._config.workbook_name
        if not workbook.is_file():
            raise FileNotFoundError(f"Fixed template workbook is missing: {workbook}")
        source_manifest = build_file_manifest(source_dir)
        job_dir = self._config.output_dir.resolve() / job_id
        if job_dir.exists():
            raise FileExistsError(f"Output job directory already exists: {job_dir}")
        template_dir = job_dir / "staging" / self._config.archive_root_name
        template_dir.parent.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copytree(
                source_dir,
                template_dir,
                ignore=shutil.ignore_patterns("~$*"),
            )
            self._remove_source_assets_from_copy(template_dir)
            assert_manifest_unchanged(source_dir, source_manifest)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        return PreparedTemplate(
            job_id=job_id,
            job_dir=job_dir,
            staging_dir=template_dir.parent,
            template_dir=template_dir,
            archive_path=job_dir / self._config.archive_name,
            source_manifest=source_manifest,
        )

    def assert_source_unchanged(self, prepared: PreparedTemplate) -> None:
        assert_manifest_unchanged(self._config.source_dir, prepared.source_manifest)

    def _remove_source_assets_from_copy(self, template_dir: Path) -> None:
        """Keep only the workbook; all copied screenshots belong to historical examples."""

        for path in template_dir.iterdir():
            if path.name == self._config.workbook_name:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
