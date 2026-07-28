"""Shared fixed-template writer results and integrity errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class TemplateIntegrityError(RuntimeError):
    """Raised when a workbook no longer matches the fixed template contract."""


@dataclass(frozen=True)
class WorkbookInspection:
    sheet_names: tuple[str, ...]
    referenced_assets: tuple[str, ...]


@dataclass(frozen=True)
class WorkbookWriteResult:
    workbook_path: Path
    inspection: WorkbookInspection
