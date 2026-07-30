"""Typed manual-override values captured in the review workspace.

Manual values are human truth: they always win over crawled fields at export
time and are persisted per job directory so a review session can be resumed.
Only template fields that already exist in the fixed workbook can be
overridden; the immutable contract never gains new columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


#: Fields an operator may edit.  ``content`` maps to ``PageData.content_text``
#: and ``published_at`` is stored as an ISO-8601 string.  ``platform`` updates
#: the routed sheet's 发布平台 enum (validated before export).
OVERRIDEABLE_FIELDS: tuple[str, ...] = (
    "title",
    "content",
    "author_name",
    "author_id",
    "account_uin",
    "store_name",
    "published_at",
    "text_type",
    "platform",
)


@dataclass
class ManualOverride:
    evidence_id: int
    values: dict[str, str] = field(default_factory=dict)
    primary_screenshot_name: str | None = None
    author_screenshot_name: str | None = None
    attachment_names: list[str] = field(default_factory=list)
    note: str = ""
    updated_at: datetime | None = None

    def is_empty(self) -> bool:
        return (
            not any(str(value).strip() for value in self.values.values())
            and not self.primary_screenshot_name
            and not self.author_screenshot_name
            and not self.attachment_names
            and not self.note.strip()
        )

    def set_value(self, field: str, value: str) -> None:
        if field not in OVERRIDEABLE_FIELDS:
            raise KeyError(f"Field {field!r} is not manually overrideable.")
        self.values[field] = value

    def clear_value(self, field: str) -> None:
        self.values.pop(field, None)
