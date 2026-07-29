"""Chromium launch/context option builders with anti-detection defaults."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.config.settings import TaskConfig
from src.screenshot.browser_runtime import mask_proxy


logger = logging.getLogger(__name__)

STEALTH_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "libs" / "stealth.min.js"

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
    options: dict[str, Any] = {
        "headless": config.headless,
        "args": launch_args,
    }
    if config.proxy_url:
        options["proxy"] = {"server": config.proxy_url}
        logger.info("Browser configured with proxy: %s", mask_proxy(config.proxy_url))
    return options


def browser_context_options(
    config: TaskConfig,
    storage_state: Any | None = None,
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
    }
    if config.user_agent:
        options["user_agent"] = config.user_agent
    if storage_state is not None:
        options["storage_state"] = storage_state
    return options
