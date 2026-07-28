"""Typed runtime models shared by input, crawler, assets and fixed-template export."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class RecordStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CRAWLED = "crawled"
    ROUTED = "routed"
    ASSETS_READY = "assets_ready"
    READY_FOR_EXPORT = "ready_for_export"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPORTED = "exported"


class ExtractionSource(StrEnum):
    PLATFORM_DOM = "platform_dom"
    JSON_LD = "json_ld"
    EMBEDDED_JSON = "embedded_json"
    NETWORK_JSON = "network_json"
    META = "meta"
    GENERIC_DOM = "generic_dom"
    VISIBLE_TEXT = "visible_text"
    DERIVED_URL = "derived_url"
    NICKNAME_FALLBACK = "nickname_fallback"
    OCR = "ocr"
    SYSTEM_MARKER = "system_marker"


class ContentKind(StrEnum):
    UNKNOWN = "unknown"
    TEXT = "text"
    IMAGE_WITH_TEXT = "image_with_text"
    IMAGE_WITHOUT_TEXT = "image_without_text"
    MIXED_TEXT_AND_IMAGE = "mixed_text_and_image"
    VIDEO_ONLY = "video_only"


class OcrStatus(StrEnum):
    NOT_RUN = "not_run"
    SUCCESS = "success"
    NO_TEXT = "no_text"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True)
class UrlTask:
    evidence_id: int
    original_url: str
    normalized_url: str

    def __post_init__(self) -> None:
        if self.evidence_id < 1:
            raise ValueError("evidence_id must start at 1.")
        if not self.original_url or not self.normalized_url:
            raise ValueError("A URL task requires original and normalized URLs.")


@dataclass
class PageData:
    final_url: str | None = None
    title: str | None = None
    content_text: str | None = None
    content_summary: str | None = None
    author_name: str | None = None
    author_id: str | None = None
    author_url: str | None = None
    account_uin: str | None = None
    store_name: str | None = None
    published_at: datetime | None = None
    published_at_raw: str | None = None
    image_urls: list[str] = field(default_factory=list)
    status_code: int | None = None
    redirect_chain: list[str] = field(default_factory=list)
    text_type_hint: str = "正文"
    field_sources: dict[str, ExtractionSource] = field(default_factory=dict)
    field_confidences: dict[str, float] = field(default_factory=dict)
    summary_truncated: bool = False
    author_id_is_fallback: bool = False
    ocr_text: str | None = None
    ocr_status: OcrStatus = OcrStatus.NOT_RUN
    ocr_image_count: int = 0
    ocr_text_image_count: int = 0
    content_kind: ContentKind = ContentKind.UNKNOWN
    original_content_chars: int = 0
    exported_content_chars: int = 0


@dataclass(frozen=True)
class RouteDecision:
    sheet_name: str
    platform_value: str
    text_type: str


@dataclass
class AssetSet:
    page_screenshot: Path | None = None
    author_screenshot: Path | None = None
    downloaded_images: list[Path] = field(default_factory=list)

    def attachment_paths(self) -> list[Path]:
        # The delivery contract intentionally exposes at most two screenshots:
        # one content-page screenshot and one optional author-home screenshot.
        # downloaded_images are transient OCR inputs and must never become ZIP
        # attachments.
        return [self.author_screenshot] if self.author_screenshot else []


@dataclass(frozen=True)
class TaskError:
    stage: str
    code: str
    message: str
    retryable: bool = False


@dataclass
class RecordResult:
    task: UrlTask
    status: RecordStatus = RecordStatus.PENDING
    page: PageData = field(default_factory=PageData)
    route: RouteDecision | None = None
    assets: AssetSet = field(default_factory=AssetSet)
    errors: list[TaskError] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempt_count: int = 0

    def add_error(self, error: TaskError, status: RecordStatus = RecordStatus.FAILED) -> None:
        self.errors.append(error)
        self.status = status

    @property
    def elapsed_seconds(self) -> float | None:
        if not self.started_at or not self.finished_at:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds())


@dataclass(frozen=True)
class TaskEvent:
    evidence_id: int
    status: RecordStatus
    stage: str
    message: str


@dataclass(frozen=True)
class TemplateRow:
    sheet_name: str
    evidence_id: int
    values_by_column: dict[str, object]
    primary_screenshot_name: str | None = None
    attachment_names: tuple[str, ...] = ()

    def all_asset_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (self.primary_screenshot_name, *self.attachment_names)
            if name
        )


@dataclass(frozen=True)
class InputReadResult:
    tasks: tuple[UrlTask, ...]
    rejected_values: tuple[str, ...]
    source_path: Path

    @property
    def duplicate_or_invalid_count(self) -> int:
        return len(self.rejected_values)
