"""Keep only files referenced by rows in a prepared template directory."""

from dataclasses import replace
import logging
from pathlib import Path
import re
from typing import Any

from src.domain.models import TemplateRow
from src.screenshot.author_evidence import read_decision


logger = logging.getLogger(__name__)

_AUTHOR_ASSET_PATTERN = re.compile(r"^\d{3}主页\.(?:jpg|jpeg|png|webp)$", re.I)


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


def audit_staged_author_assets(
    template_dir: Path,
    rows: list[TemplateRow],
) -> tuple[list[TemplateRow], list[dict[str, Any]]]:
    """Remove author-home screenshots that lack an accepted evidence decision.

    Every ``NNN主页.<image>`` file must have a persisted
    :class:`AuthorEvidenceDecision` sidecar with ``accepted=True``.  Orphaned,
    identity-mismatched or overlay-blocked screenshots are deleted from the
    staging copy, their names are stripped from the workbook screenshot and
    attachment references, and an audit entry is returned for the
    pending-manual-entry report.  The source template is never touched.
    """

    updated_rows = list(rows)
    entries: list[dict[str, Any]] = []
    for path in sorted(Path(template_dir).glob("*主页.*")):
        if path.is_dir() or not _AUTHOR_ASSET_PATTERN.match(path.name):
            continue
        sidecar = path.with_suffix(".decision.json")
        decision = read_decision(sidecar)
        if decision is not None and decision.accepted:
            continue
        rejection = (
            decision.rejection_code
            if decision is not None and decision.rejection_code
            else "AUTHOR_DECISION_MISSING"
        )
        try:
            path.unlink()
        except OSError as error:
            logger.warning("Unable to remove rejected author asset %s: %s", path, error)
            continue
        updated_rows = [
            _strip_asset_reference(row, path.name) for row in updated_rows
        ]
        entries.append(
            {
                "file": path.name,
                "evidence_id": decision.evidence_id if decision is not None else None,
                "rejection_code": rejection,
                "action": "removed_from_staging",
            }
        )
        logger.info(
            "Removed author asset %s from staging (rejection=%s)",
            path.name,
            rejection,
        )
    return updated_rows, entries


def _strip_asset_reference(row: TemplateRow, asset_name: str) -> TemplateRow:
    # 对调表（homepage_screenshot_primary）把个人主页截图写在主截图列，
    # 因此主截图槽位与附件槽位都要剥离。
    primary = row.primary_screenshot_name
    if primary == asset_name:
        primary = None
    remaining = tuple(name for name in row.attachment_names if name != asset_name)
    if primary == row.primary_screenshot_name and remaining == tuple(row.attachment_names):
        return row
    for column, value in list(row.values_by_column.items()):
        if not isinstance(value, str) or asset_name not in value:
            continue
        kept = [part for part in value.split(",") if part.strip() != asset_name]
        if kept:
            row.values_by_column[column] = ",".join(kept)
        else:
            del row.values_by_column[column]
    return replace(row, primary_screenshot_name=primary, attachment_names=remaining)
