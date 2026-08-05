"""Atomic per-job persistence for manual overrides.

Overrides live in ``manual_overrides.json`` inside the job output directory
(next to ``job_checkpoint.json``) so they survive application restarts and are
never packaged into ``template.zip``.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any

from src.domain.overrides import OVERRIDEABLE_FIELDS, ManualOverride
from src.services import recovery_mirror
from src.utils.file_utils import atomic_replace, require_safe_file_name
from src.utils.time_utils import DEFAULT_TIMEZONE

OVERRIDES_FILE_NAME = "manual_overrides.json"
OVERRIDES_SCHEMA_VERSION = 1


class ManualOverrideStore:
    def __init__(self, job_dir: Path) -> None:
        self.path = Path(job_dir) / OVERRIDES_FILE_NAME
        self._overrides: dict[int, ManualOverride] = {}

    def load(self) -> "ManualOverrideStore":
        self._overrides = {}
        if not self.path.exists():
            self._restore_from_mirror()
        if not self.path.exists():
            return self
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != OVERRIDES_SCHEMA_VERSION:
            raise ValueError(f"Unsupported manual overrides schema in {self.path}")
        for entry in payload.get("overrides", []):
            override = _override_from_dict(entry)
            if override is not None:
                self._overrides[override.evidence_id] = override
        return self

    def save(self) -> None:
        payload = {
            "schema_version": OVERRIDES_SCHEMA_VERSION,
            "overrides": [
                _override_to_dict(override)
                for override in sorted(
                    self._overrides.values(),
                    key=lambda item: item.evidence_id,
                )
                if not override.is_empty()
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        atomic_replace(temporary, self.path)
        recovery_mirror.mirror_file(self.path.parent.name, self.path)

    def _restore_from_mirror(self) -> None:
        mirrored = recovery_mirror.mirrored_json(self.path.parent.name, OVERRIDES_FILE_NAME)
        if mirrored is None:
            return
        try:
            shutil.copy2(mirrored, self.path)
        except OSError:
            pass

    def get(self, evidence_id: int) -> ManualOverride | None:
        return self._overrides.get(evidence_id)

    def all(self) -> tuple[ManualOverride, ...]:
        return tuple(
            override
            for override in sorted(
                self._overrides.values(),
                key=lambda item: item.evidence_id,
            )
            if not override.is_empty()
        )

    def get_or_create(self, evidence_id: int) -> ManualOverride:
        override = self._overrides.get(evidence_id)
        if override is None:
            override = ManualOverride(evidence_id=evidence_id)
            self._overrides[evidence_id] = override
        return override

    def set_field(self, evidence_id: int, field: str, value: str) -> ManualOverride:
        override = self.get_or_create(evidence_id)
        if value.strip():
            override.set_value(field, value)
        else:
            override.clear_value(field)
        return self._touch(override)

    def set_primary_screenshot(
        self,
        evidence_id: int,
        name: str | None,
    ) -> ManualOverride:
        override = self.get_or_create(evidence_id)
        override.primary_screenshot_name = (
            require_safe_file_name(name) if name else None
        )
        return self._touch(override)

    def set_author_screenshot(
        self,
        evidence_id: int,
        name: str | None,
    ) -> ManualOverride:
        override = self.get_or_create(evidence_id)
        override.author_screenshot_name = (
            require_safe_file_name(name) if name else None
        )
        return self._touch(override)

    def set_attachments(self, evidence_id: int, names: list[str]) -> ManualOverride:
        override = self.get_or_create(evidence_id)
        override.attachment_names = [require_safe_file_name(name) for name in names]
        return self._touch(override)

    def set_note(self, evidence_id: int, note: str) -> ManualOverride:
        override = self.get_or_create(evidence_id)
        override.note = note
        return self._touch(override)

    def remove(self, evidence_id: int) -> None:
        if evidence_id in self._overrides:
            del self._overrides[evidence_id]
            self.save()

    def _touch(self, override: ManualOverride) -> ManualOverride:
        override.updated_at = datetime.now(DEFAULT_TIMEZONE)
        if override.is_empty():
            self._overrides.pop(override.evidence_id, None)
        self.save()
        return override


def _override_to_dict(override: ManualOverride) -> dict[str, Any]:
    return {
        "evidence_id": override.evidence_id,
        "values": {
            field: value
            for field, value in override.values.items()
            if field in OVERRIDEABLE_FIELDS
        },
        "primary_screenshot_name": override.primary_screenshot_name,
        "author_screenshot_name": override.author_screenshot_name,
        "attachment_names": list(override.attachment_names),
        "note": override.note,
        "updated_at": override.updated_at.isoformat() if override.updated_at else None,
    }


def _override_from_dict(entry: Any) -> ManualOverride | None:
    if not isinstance(entry, dict):
        return None
    try:
        evidence_id = int(entry["evidence_id"])
    except (KeyError, TypeError, ValueError):
        return None
    values = entry.get("values")
    override = ManualOverride(
        evidence_id=evidence_id,
        values={
            str(field): str(value)
            for field, value in (values.items() if isinstance(values, dict) else [])
            if str(field) in OVERRIDEABLE_FIELDS
        },
        primary_screenshot_name=_safe_name_or_none(entry.get("primary_screenshot_name")),
        author_screenshot_name=_safe_name_or_none(entry.get("author_screenshot_name")),
        attachment_names=[
            name
            for name in (
                _safe_name_or_none(item) for item in entry.get("attachment_names", [])
            )
            if name
        ],
        note=str(entry.get("note") or ""),
        updated_at=_parse_datetime(entry.get("updated_at")),
    )
    return override


def _safe_name_or_none(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return require_safe_file_name(value.strip())
    except Exception:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None
