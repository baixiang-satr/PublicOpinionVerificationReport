"""Application configuration with immutable template and mutable task settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TemplateConfig:
    """Settings that define the fixed delivery contract and cannot be changed by the UI."""

    source_dir: Path = PROJECT_ROOT / "template"
    output_dir: Path = PROJECT_ROOT / "output"
    workbook_name: str = "template.xlsx"
    archive_name: str = "template.zip"
    archive_root_name: str = "template"

    def __post_init__(self) -> None:
        if self.workbook_name != "template.xlsx":
            raise ValueError("The fixed workbook name must be template.xlsx.")
        if self.archive_name != "template.zip":
            raise ValueError("The fixed archive name must be template.zip.")
        if self.archive_root_name != "template":
            raise ValueError("The fixed archive root must be template.")

    @property
    def workbook_path(self) -> Path:
        return self.source_dir / self.workbook_name


@dataclass(frozen=True)
class TaskConfig:
    """Per-task options that may later be surfaced in the desktop interface."""

    max_concurrency: int = 3
    page_timeout_seconds: int = 30
    max_retries: int = 2
    retry_base_delay_seconds: float = 1.0
    min_host_interval_seconds: float = 1.0
    page_stabilize_milliseconds: int = 500
    screenshot_format: str = "jpeg"
    full_page_screenshot: bool = True
    max_images_per_record: int = 20
    max_image_bytes: int = 10 * 1024 * 1024
    summary_max_chars: int = 2_000
    timezone: str = "Asia/Shanghai"
    headless: bool = True
    storage_state_path: Path | None = None
    allow_nickname_as_id: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= 10:
            raise ValueError("max_concurrency must be between 1 and 10.")
        if self.page_timeout_seconds <= 0 or self.max_retries < 0:
            raise ValueError("Timeout must be positive and retries cannot be negative.")
        if self.retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds cannot be negative.")
        if self.min_host_interval_seconds < 0:
            raise ValueError("min_host_interval_seconds cannot be negative.")
        if self.page_stabilize_milliseconds < 0:
            raise ValueError("page_stabilize_milliseconds cannot be negative.")
        if self.screenshot_format not in {"jpeg", "png"}:
            raise ValueError("screenshot_format must be jpeg or png.")
        if self.max_images_per_record < 0 or self.max_image_bytes <= 0:
            raise ValueError("Image limits must be non-negative and positive respectively.")
        if not 1 <= self.summary_max_chars <= 32_767:
            raise ValueError("summary_max_chars must be between 1 and 32767.")


@dataclass(frozen=True)
class AppConfig:
    """Top-level configuration passed explicitly to services instead of global state."""

    template: TemplateConfig = field(default_factory=TemplateConfig)
    task: TaskConfig = field(default_factory=TaskConfig)

    @classmethod
    def defaults(cls, project_root: Path | None = None) -> "AppConfig":
        root = project_root or PROJECT_ROOT
        return cls(template=TemplateConfig(source_dir=root / "template", output_dir=root / "output"))

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> "AppConfig":
        """Read only task-level overrides; template names remain fixed by contract."""

        defaults = cls.defaults(project_root)
        task = TaskConfig(
            max_concurrency=int(os.getenv("POR_MAX_CONCURRENCY", defaults.task.max_concurrency)),
            page_timeout_seconds=int(os.getenv("POR_PAGE_TIMEOUT_SECONDS", defaults.task.page_timeout_seconds)),
            max_retries=int(os.getenv("POR_MAX_RETRIES", defaults.task.max_retries)),
            retry_base_delay_seconds=float(
                os.getenv("POR_RETRY_BASE_DELAY_SECONDS", defaults.task.retry_base_delay_seconds)
            ),
            min_host_interval_seconds=float(
                os.getenv("POR_MIN_HOST_INTERVAL_SECONDS", defaults.task.min_host_interval_seconds)
            ),
            page_stabilize_milliseconds=int(
                os.getenv("POR_PAGE_STABILIZE_MILLISECONDS", defaults.task.page_stabilize_milliseconds)
            ),
            screenshot_format=os.getenv("POR_SCREENSHOT_FORMAT", defaults.task.screenshot_format),
            headless=os.getenv("POR_HEADLESS", str(defaults.task.headless)).lower() in {"1", "true", "yes"},
            storage_state_path=_path_from_environment("POR_STORAGE_STATE_PATH"),
            allow_nickname_as_id=os.getenv(
                "POR_ALLOW_NICKNAME_AS_ID", str(defaults.task.allow_nickname_as_id)
            ).lower()
            in {"1", "true", "yes"},
        )
        return cls(template=defaults.template, task=task)


def _path_from_environment(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value).expanduser() if value else None
