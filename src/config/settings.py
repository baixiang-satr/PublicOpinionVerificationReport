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

    # ── Core crawling ─────────────────────────────────────────────────
    max_concurrency: int = 3
    page_timeout_seconds: int = 30
    max_retries: int = 2
    retry_base_delay_seconds: float = 2.0
    min_host_interval_seconds: float = 3.0
    page_stabilize_milliseconds: int = 1500

    # ── Screenshot ────────────────────────────────────────────────────
    screenshot_format: str = "jpeg"
    full_page_screenshot: bool = True

    # ── OCR image inputs ──────────────────────────────────────────────
    # Page images are temporary OCR inputs only; final output contains at
    # most the content-page and author-home screenshots.
    max_images_per_record: int = 6
    max_image_bytes: int = 10 * 1024 * 1024

    # ── Content extraction ────────────────────────────────────────────
    summary_max_chars: int = 2_000

    # ── Locale & timezone ─────────────────────────────────────────────
    timezone: str = "Asia/Shanghai"

    # ── Browser mode ──────────────────────────────────────────────────
    headless: bool = True
    storage_state_path: Path | None = None
    manual_intervention_timeout_seconds: int = 90

    # ── Author / OCR ──────────────────────────────────────────────────
    allow_nickname_as_id: bool = True
    ocr_enabled: bool = True
    ocr_confidence_threshold: float = 0.5

    # ──────────────────────────────────────────────────────────────────
    # Anti-detection / anti-crawling options
    # 参考 MediaCrawler 的反爬虫对抗策略:
    #   - stealth.min.js 注入 (stealth)
    #   - 代理 IP 轮换 (proxy)
    #   - 可选自定义 User-Agent (user_agent)
    #   - 固定截图视口 (viewport)
    # ──────────────────────────────────────────────────────────────────

    # ── Stealth anti-detection ────────────────────────────────────────
    # 是否注入 stealth.min.js 来隐藏 Playwright 自动化痕迹
    # 参考: MediaCrawler 中 browser_context.add_init_script(path="libs/stealth.min.js")
    enable_stealth: bool = True

    # 是否应用额外的 JS 补丁 (webdriver、permissions 等覆盖)
    enable_extra_stealth: bool = True

    # ── Proxy ─────────────────────────────────────────────────────────
    # HTTP/HTTPS/SOCKS5 代理 URL，例如 "http://user:pass@host:port"
    # 参考: MediaCrawler 的 ProxyIpPool 自动轮换代理
    proxy_url: str | None = None

    # ── User-Agent ────────────────────────────────────────────────────
    # 自定义 User-Agent；设为 None 时使用 Chromium 原生 User-Agent，
    # 避免 UA、浏览器内核和桌面视口互相矛盾。
    user_agent: str | None = None

    # ── Browser fingerprint ───────────────────────────────────────────
    # 固定视口尺寸，确保截图可复现且不会因 2x DPR 产生超大图片。
    viewport_width: int = 1440
    viewport_height: int = 900

    # ── Extra Chromium args ───────────────────────────────────────────
    # 额外的 Chromium 命令行参数，例如:
    #   ["--window-size=1920,1080", "--disable-webgl"]
    extra_chromium_args: tuple[str, ...] = ()

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
        if not 0 <= self.manual_intervention_timeout_seconds <= 600:
            raise ValueError("manual_intervention_timeout_seconds must be between 0 and 600.")
        if not 0.0 <= self.ocr_confidence_threshold <= 1.0:
            raise ValueError("ocr_confidence_threshold must be between 0 and 1.")
        if self.viewport_width < 800 or self.viewport_height < 600:
            raise ValueError("viewport dimensions must be at least 800x600.")
        if self.proxy_url and not self.proxy_url.startswith(("http://", "https://", "socks5://", "socks4://")):
            raise ValueError("proxy_url must start with http://, https://, socks5://, or socks4://")


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

        def _bool_env(name: str, default: bool) -> bool:
            return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}

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
            full_page_screenshot=_bool_env("POR_FULL_PAGE_SCREENSHOT", defaults.task.full_page_screenshot),
            headless=_bool_env("POR_HEADLESS", defaults.task.headless),
            storage_state_path=_path_from_environment("POR_STORAGE_STATE_PATH"),
            manual_intervention_timeout_seconds=int(
                os.getenv(
                    "POR_MANUAL_INTERVENTION_TIMEOUT_SECONDS",
                    defaults.task.manual_intervention_timeout_seconds,
                )
            ),
            allow_nickname_as_id=_bool_env("POR_ALLOW_NICKNAME_AS_ID", defaults.task.allow_nickname_as_id),
            ocr_enabled=_bool_env("POR_OCR_ENABLED", defaults.task.ocr_enabled),
            ocr_confidence_threshold=float(
                os.getenv("POR_OCR_CONFIDENCE", defaults.task.ocr_confidence_threshold)
            ),
            # ── Anti-detection overrides ──────────────────────────────
            enable_stealth=_bool_env("POR_ENABLE_STEALTH", defaults.task.enable_stealth),
            enable_extra_stealth=_bool_env("POR_ENABLE_EXTRA_STEALTH", defaults.task.enable_extra_stealth),
            proxy_url=os.getenv("POR_PROXY_URL", defaults.task.proxy_url) or None,
            user_agent=os.getenv("POR_USER_AGENT", defaults.task.user_agent) or None,
            viewport_width=int(os.getenv("POR_VIEWPORT_WIDTH", str(defaults.task.viewport_width))),
            viewport_height=int(os.getenv("POR_VIEWPORT_HEIGHT", str(defaults.task.viewport_height))),
            extra_chromium_args=_parse_extra_args(os.getenv("POR_EXTRA_CHROMIUM_ARGS", "")),
        )
        return cls(template=defaults.template, task=task)


def _parse_extra_args(raw: str) -> tuple[str, ...]:
    """Parse comma-separated extra Chromium args from env var."""
    if not raw or not raw.strip():
        return ()
    return tuple(arg.strip() for arg in raw.split(",") if arg.strip())


def _path_from_environment(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value).expanduser() if value else None
