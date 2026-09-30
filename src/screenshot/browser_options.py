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
    # 注：不得加入 --disable-web-security —— 实测会让微信视频号
    # finder-preview SPA 启动后渲染空壳（2026-08-04 二分定位）。
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


def fixed_window_geometry_args(config: TaskConfig) -> tuple[str, str]:
    """Launch args pinning the window to the configured viewport at scale 1.

    ``--window-size`` is interpreted in physical pixels on Windows: on a
    125%/150% display the window content area would shrink below the fixed
    viewport and PrintWindow evidence captures come out cropped/shifted.
    ``--force-device-scale-factor=1`` keeps DIP == physical pixel so every
    machine lays pages out identically (same approach as
    ``interactive_login_launch_options``).
    """

    return (
        f"--window-size={config.viewport_width},{config.viewport_height}",
        "--force-device-scale-factor=1",
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
                    *fixed_window_geometry_args(config),
                    # Suppress the "Restore pages?" infobar after an unclean
                    # shutdown: it pushes page content down inside the
                    # captured window and ruins evidence framing.
                    "--hide-crash-restore-bubble",
                    "--disable-session-crashed-bubble",
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


def interactive_login_launch_options(config: TaskConfig) -> dict[str, Any]:
    """Launch options for the human-operated interactive login window.

    Pinned to the bundled Chromium (no ``channel``) so every machine runs
    the byte-identical browser shipped in ``ms-playwright/``;
    ``browser_channel``/``POR_BROWSER_CHANNEL`` only steer the crawl and
    screenshot browsers.  Software rendering stays on: login pages never
    need proprietary video decode, and dropping the GPU removes the
    driver-dependent white-flash repaint reported on some machines.
    ``--force-device-scale-factor=1`` matches the fixed context
    ``device_scale_factor`` so 125%/150% displays lay out like 100% ones.
    """

    launch_args = [
        *ANTI_DETECTION_ARGS,
        *config.extra_chromium_args,
        "--force-device-scale-factor=1",
    ]
    options: dict[str, Any] = {
        "headless": False,
        "args": launch_args,
    }
    if config.proxy_url:
        options["proxy"] = {"server": config.proxy_url}
        logger.info(
            "Interactive login browser configured with proxy: %s",
            mask_proxy(config.proxy_url),
        )
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
            browser = await playwright.chromium.launch(**options)
        except Exception as error:  # noqa: BLE001 — 尝试下一个候选
            last_error = error
            logger.warning("Headed launch with channel=%s failed: %s", channel, error)
            continue
        # Record the channel that actually won: cross-machine evidence
        # differences are otherwise impossible to attribute (Edge vs Chrome
        # vs bundled Chromium render different chrome/UA).
        try:
            version = browser.version
        except Exception:  # noqa: BLE001 — 版本探测失败不阻断启动
            version = "unknown"
        logger.info(
            "Headed browser launched: channel=%s version=%s",
            channel or "chromium(bundled)",
            version,
        )
        return browser
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
