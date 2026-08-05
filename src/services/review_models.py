"""Review workspace view models (split from review_session to keep it under the line cap)."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.models import RecordStatus

_SOURCE_TEXT = {"manual": "人工", "crawled": "抓取", "empty": ""}


@dataclass(frozen=True)
class ReviewFieldView:
    field: str
    label: str
    value: str
    source: str  # "manual" | "crawled" | "empty"
    required: bool
    missing: bool
    multiline: bool
    choices: tuple[str, ...] = ()

    @property
    def source_text(self) -> str:
        return _SOURCE_TEXT.get(self.source, "")


@dataclass(frozen=True)
class ReviewRecordSummary:
    evidence_id: int
    original_url: str
    final_url: str | None
    sheet_name: str
    platform_value: str
    status: RecordStatus
    missing_labels: tuple[str, ...]
    has_override: bool

    @property
    def needs_attention(self) -> bool:
        if self.missing_labels:
            return True
        if self.has_override:
            # A human already reviewed and completed this record.
            return False
        return self.status in {
            RecordStatus.NEEDS_REVIEW,
            RecordStatus.FAILED,
        }
