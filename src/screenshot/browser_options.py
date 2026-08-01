"""Chromium launch/context option builders with anti-detection defaults."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.screenshot.browser_runtime import mask_proxy

logger = logging.getLogger(__name__)

STEALTH_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "libs" / "stealth.min.js"

# Kuaishou returns a tiny JSON error document to automated desktop clients,
# while its official mobile share surface provides SSR HTML, the requested
# photo in INIT_STATE, and a renderable evidence page.
KUAISHOU_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)

# ── Anti-detection Chromium launch arguments ─────────────────────────────
# Reference: MediaCrawler (https://github.com/NanmiCoder/MediaCrawler)
# These args help hide Playwright automation fingerprints from target sites.
ANTI_DETECTION_ARGS = (
    # ── Core automation hiding ───────────────────────────────────────
    "--disable-blink-features=AutomationControlled",
    "--exclude-switches=enable-automation",
    "--disable-infobars",
    # ── Sandbox / shared memory ──────────────────────────────────────
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    # ── Background throttling prevention ─────────────────────────────
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--disable-hang-monitor",
    # ── Feature flags ────────────────────────────────────────────────
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-web-security",
    "--disable-sync",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    # ── Performance ──────────────────────────────────────────────────
    "--disable-gpu",
    # ── Misc ─────────────────────────────────────────────────────────
    "--no-first-run",
    "--no-default-browser-check",
    "--hide-scrollbars",
    "--mute-audio",
)


def browser_launch_options(config: TaskConfig) -> dict[str, Any]:
    launch_args = [*ANTI_DETECTION_ARGS, *config.extra_chromium_args]
    if not config.headless:
        # Headed windows must render video: --disable-gpu forces software
        # decode paths that leave douyin players black.
        launch_args = [arg for arg in launch_args if arg != "--disable-gpu"]
        if config.background_crawl_browser:
            launch_args.extend(
                (
                    "--window-position=-32000,-32000",
                    f"--window-size={config.viewport_width},{config.viewport_height}",
                )
            )
    options: dict[str, Any] = {
        "headless": config.headless,
        "args": launch_args,
    }
    if config.browser_channel:
        options["channel"] = config.browser_channel
    if config.proxy_url:
        options["proxy"] = {"server": config.proxy_url}
        logger.info("Browser configured with proxy: %s", mask_proxy(config.proxy_url))
    return options


def headed_channel_candidates(config: TaskConfig) -> tuple[str | None, ...]:
    """Channels to try for interactive (headed) browsers, best first.

    Real Edge/Chrome ships proprietary H.264/H.265 codecs (black video fix)
    and carries a genuine browser fingerprint (fewer risk-control login
    popups); bundled Chromium remains the final fallback.
    """

    if config.browser_channel:
        return (config.browser_channel, "msedge", "chrome", None)
    return ("msedge", "chrome", None)


async def launch_headed_with_fallback(
    playwright: Any,
    config: TaskConfig,
    launch_options: dict[str, Any],
) -> Any:
    """Launch a headed browser trying each channel candidate in order."""

    last_error: Exception | None = None
    for channel in headed_channel_candidates(config):
        options = dict(launch_options)
        options.pop("channel", None)
        if channel:
            options["channel"] = channel
        try:
            return await playwright.chromium.launch(**options)
        except Exception as error:  # noqa: BLE001 — 尝试下一个候选
            last_error = error
            logger.warning("Headed launch with channel=%s failed: %s", channel, error)
    raise RuntimeError(
        "Unable to launch a headed browser with any channel candidate."
    ) from last_error


def browser_context_options(
    config: TaskConfig,
    storage_state: Any | None = None,
    *,
    platform_key: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
        },
        "locale": "zh-CN",
        "timezone_id": config.timezone,
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "color_scheme": "light",
        "reduced_motion": "no-preference",
        "forced_colors": "none",
        "extra_http_headers": {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "DNT": "1",
        },
    }
    if config.user_agent:
        options["user_agent"] = config.user_agent
    elif platform_key == "kuaishou":
        options["user_agent"] = KUAISHOU_MOBILE_USER_AGENT
    if storage_state is not None:
        options["storage_state"] = storage_state
    return options
