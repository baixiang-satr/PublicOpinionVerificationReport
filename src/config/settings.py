"""Application configuration with immutable template and mutable task settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import sys


def _project_root() -> Path:
    """PyInstaller 打包后资源与输出放在 exe 同级目录。"""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()


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
    page_processing_timeout_seconds: float = 240.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 2.0
    min_host_interval_seconds: float = 3.0
    page_stabilize_milliseconds: int = 1500

    # ── Screenshot ────────────────────────────────────────────────────
    screenshot_format: str = "jpeg"
    full_page_screenshot: bool = True
    max_full_page_screenshot_height: int = 4_096
    screenshot_jpeg_quality: int = 90
    long_page_jpeg_quality: int = 82

    # ── OCR image inputs ──────────────────────────────────────────────
    # Page images are temporary OCR inputs only; final output contains at
    # most the content-page and author-home screenshots.
    max_images_per_record: int = 6
    max_image_bytes: int = 10 * 1024 * 1024

    # ── Content extraction ────────────────────────────────────────────
    summary_max_chars: int = 2_000
    export_content_max_chars: int = 32_000
    capture_network_json: bool = True
    max_structured_payload_bytes: int = 2_000_000
    max_structured_payloads: int = 24
    enable_platform_fallbacks: bool = True

    # ── Locale & timezone ─────────────────────────────────────────────
    timezone: str = "Asia/Shanghai"

    # ── Browser mode ──────────────────────────────────────────────────
    # Evidence crawling always uses a visible browser.  Keeping the flag in
    # the stable config contract avoids breaking older checkpoints/UI
    # payloads, but production construction and BrowserPool both force False.
    headless: bool = False
    # Keep the real headed browser fingerprint/codecs, but place automatic
    # crawl windows off-screen so batch work does not steal keyboard focus.
    # Login and manual-capture windows explicitly override this to False.
    background_crawl_browser: bool = True
    # Legacy combined Playwright state. New verified states live in
    # auth_store_dir and are isolated by platform.
    storage_state_path: Path | None = None
    auth_store_dir: Path | None = None
    manual_intervention_timeout_seconds: int = 90
    enable_auth_health_gate: bool = True
    pause_platform_on_auth_failure: bool = True

    # ── Author / OCR ──────────────────────────────────────────────────
    allow_nickname_as_id: bool = True
    ocr_enabled: bool = True
    ocr_confidence_threshold: float = 0.5
    ocr_python_executable: Path | None = None
    ocr_worker_timeout_seconds: float = 45.0
    ocr_max_restarts: int = 1
    ocr_min_image_width: int = 80
    ocr_min_image_height: int = 80

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

    # ── Browser channel ───────────────────────────────────────────────
    # Playwright 浏览器内核通道（"msedge"/"chrome"）；None 使用内置
    # Chromium。截图与有头兜底浏览器始终优先 msedge（含专有视频解码器，
    # 真实指纹不易触发风控），本项用于全局切换爬取浏览器。
    browser_channel: str | None = None

    # ── Legacy headed fallback switch ────────────────────────────────
    # 保留旧配置兼容；主抓取现已始终使用可见浏览器，通常不会进入二次兜底。
    enable_headed_fallback: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= 10:
            raise ValueError("max_concurrency must be between 1 and 10.")
        if (
            self.page_timeout_seconds <= 0
            or self.page_processing_timeout_seconds <= 0
            or self.max_retries < 0
        ):
            raise ValueError("Timeout must be positive and retries cannot be negative.")
        if self.retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds cannot be negative.")
        if self.min_host_interval_seconds < 0:
            raise ValueError("min_host_interval_seconds cannot be negative.")
        if self.page_stabilize_milliseconds < 0:
            raise ValueError("page_stabilize_milliseconds cannot be negative.")
        if self.screenshot_format not in {"jpeg", "png"}:
            raise ValueError("screenshot_format must be jpeg or png.")
        if not 1_024 <= self.max_full_page_screenshot_height <= 32_767:
            raise ValueError("max_full_page_screenshot_height must be between 1024 and 32767.")
        if not 1 <= self.long_page_jpeg_quality <= self.screenshot_jpeg_quality <= 100:
            raise ValueError(
                "JPEG qualities must satisfy 1 <= long_page_jpeg_quality "
                "<= screenshot_jpeg_quality <= 100."
            )
        if self.max_images_per_record < 0 or self.max_image_bytes <= 0:
            raise ValueError("Image limits must be non-negative and positive respectively.")
        if not 1 <= self.summary_max_chars <= 32_767:
            raise ValueError("summary_max_chars must be between 1 and 32767.")
        if not 1 <= self.export_content_max_chars <= 32_767:
            raise ValueError("export_content_max_chars must be between 1 and 32767.")
        if self.max_structured_payload_bytes < 1_024:
            raise ValueError("max_structured_payload_bytes must be at least 1024.")
        if not 1 <= self.max_structured_payloads <= 100:
            raise ValueError("max_structured_payloads must be between 1 and 100.")
        if not 0 <= self.manual_intervention_timeout_seconds <= 600:
            raise ValueError("manual_intervention_timeout_seconds must be between 0 and 600.")
        if not 0.0 <= self.ocr_confidence_threshold <= 1.0:
            raise ValueError("ocr_confidence_threshold must be between 0 and 1.")
        if self.ocr_worker_timeout_seconds <= 0 or self.ocr_max_restarts < 0:
            raise ValueError("OCR timeout must be positive and restarts cannot be negative.")
        if self.ocr_min_image_width < 1 or self.ocr_min_image_height < 1:
            raise ValueError("OCR image dimensions must be positive.")
        if self.viewport_width < 800 or self.viewport_height < 600:
            raise ValueError("viewport dimensions must be at least 800x600.")
        if self.proxy_url and not self.proxy_url.startswith(("http://", "https://", "socks5://", "socks4://")):
            raise ValueError("proxy_url must start with http://, https://, socks5://, or socks4://")
        if self.browser_channel is not None and not self.browser_channel.strip():
            raise ValueError("browser_channel must be None or a non-empty channel name.")


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
            page_processing_timeout_seconds=float(
                os.getenv(
                    "POR_PAGE_PROCESSING_TIMEOUT_SECONDS",
                    defaults.task.page_processing_timeout_seconds,
                )
            ),
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
            max_full_page_screenshot_height=int(
                os.getenv(
                    "POR_MAX_FULL_PAGE_SCREENSHOT_HEIGHT",
                    defaults.task.max_full_page_screenshot_height,
                )
            ),
            screenshot_jpeg_quality=int(
                os.getenv("POR_SCREENSHOT_JPEG_QUALITY", defaults.task.screenshot_jpeg_quality)
            ),
            long_page_jpeg_quality=int(
                os.getenv("POR_LONG_PAGE_JPEG_QUALITY", defaults.task.long_page_jpeg_quality)
            ),
            headless=False,
            background_crawl_browser=_bool_env(
                "POR_BACKGROUND_CRAWL_BROWSER",
                defaults.task.background_crawl_browser,
            ),
            storage_state_path=(
                _path_from_environment("POR_STORAGE_STATE_PATH")
                or _default_storage_state_path()
            ),
            auth_store_dir=(
                _path_from_environment("POR_AUTH_STORE_DIR")
                or default_auth_store_dir()
            ),
            manual_intervention_timeout_seconds=int(
                os.getenv(
                    "POR_MANUAL_INTERVENTION_TIMEOUT_SECONDS",
                    defaults.task.manual_intervention_timeout_seconds,
                )
            ),
            enable_auth_health_gate=_bool_env(
                "POR_ENABLE_AUTH_HEALTH_GATE",
                defaults.task.enable_auth_health_gate,
            ),
            pause_platform_on_auth_failure=_bool_env(
                "POR_PAUSE_PLATFORM_ON_AUTH_FAILURE",
                defaults.task.pause_platform_on_auth_failure,
            ),
            capture_network_json=_bool_env(
                "POR_CAPTURE_NETWORK_JSON",
                defaults.task.capture_network_json,
            ),
            max_structured_payload_bytes=int(
                os.getenv(
                    "POR_MAX_STRUCTURED_PAYLOAD_BYTES",
                    defaults.task.max_structured_payload_bytes,
                )
            ),
            max_structured_payloads=int(
                os.getenv(
                    "POR_MAX_STRUCTURED_PAYLOADS",
                    defaults.task.max_structured_payloads,
                )
            ),
            enable_platform_fallbacks=_bool_env(
                "POR_ENABLE_PLATFORM_FALLBACKS",
                defaults.task.enable_platform_fallbacks,
            ),
            max_images_per_record=int(
                os.getenv(
                    "POR_MAX_IMAGES_PER_RECORD",
                    defaults.task.max_images_per_record,
                )
            ),
            max_image_bytes=int(
                os.getenv(
                    "POR_MAX_IMAGE_BYTES",
                    defaults.task.max_image_bytes,
                )
            ),
            summary_max_chars=int(
                os.getenv(
                    "POR_SUMMARY_MAX_CHARS",
                    defaults.task.summary_max_chars,
                )
            ),
            export_content_max_chars=int(
                os.getenv(
                    "POR_EXPORT_CONTENT_MAX_CHARS",
                    defaults.task.export_content_max_chars,
                )
            ),
            allow_nickname_as_id=_bool_env("POR_ALLOW_NICKNAME_AS_ID", defaults.task.allow_nickname_as_id),
            ocr_enabled=_bool_env("POR_OCR_ENABLED", defaults.task.ocr_enabled),
            ocr_confidence_threshold=float(
                os.getenv("POR_OCR_CONFIDENCE", defaults.task.ocr_confidence_threshold)
            ),
            ocr_python_executable=_path_from_environment(
                "POR_OCR_PYTHON_EXECUTABLE"
            ),
            ocr_worker_timeout_seconds=float(
                os.getenv(
                    "POR_OCR_WORKER_TIMEOUT_SECONDS",
                    defaults.task.ocr_worker_timeout_seconds,
                )
            ),
            ocr_max_restarts=int(
                os.getenv("POR_OCR_MAX_RESTARTS", defaults.task.ocr_max_restarts)
            ),
            ocr_min_image_width=int(
                os.getenv(
                    "POR_OCR_MIN_IMAGE_WIDTH",
                    defaults.task.ocr_min_image_width,
                )
            ),
            ocr_min_image_height=int(
                os.getenv(
                    "POR_OCR_MIN_IMAGE_HEIGHT",
                    defaults.task.ocr_min_image_height,
                )
            ),
            # ── Anti-detection overrides ──────────────────────────────
            enable_stealth=_bool_env("POR_ENABLE_STEALTH", defaults.task.enable_stealth),
            enable_extra_stealth=_bool_env("POR_ENABLE_EXTRA_STEALTH", defaults.task.enable_extra_stealth),
            proxy_url=os.getenv("POR_PROXY_URL", defaults.task.proxy_url) or None,
            user_agent=os.getenv("POR_USER_AGENT", defaults.task.user_agent) or None,
            viewport_width=int(os.getenv("POR_VIEWPORT_WIDTH", str(defaults.task.viewport_width))),
            viewport_height=int(os.getenv("POR_VIEWPORT_HEIGHT", str(defaults.task.viewport_height))),
            extra_chromium_args=_parse_extra_args(os.getenv("POR_EXTRA_CHROMIUM_ARGS", "")),
            browser_channel=os.getenv("POR_BROWSER_CHANNEL", defaults.task.browser_channel) or None,
            enable_headed_fallback=_bool_env(
                "POR_ENABLE_HEADED_FALLBACK",
                defaults.task.enable_headed_fallback,
            ),
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


def _default_storage_state_path() -> Path:
    """Return a user-local login-state file that is not included in exports."""

    return _local_app_data_root() / "login_state.json"


def default_auth_store_dir() -> Path:
    """Return the per-platform encrypted authentication store directory."""

    return _local_app_data_root() / "auth"


def _local_app_data_root() -> Path:
    if os.name == "nt":
        local_data = os.getenv("LOCALAPPDATA")
        root = Path(local_data) if local_data else Path.home() / "AppData" / "Local"
    else:
        root = Path(os.getenv("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return root / "PublicOpinionVerificationReport"
