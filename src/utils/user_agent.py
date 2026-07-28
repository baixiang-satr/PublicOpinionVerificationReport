"""Realistic User-Agent rotation to avoid browser fingerprint-based blocking.

参考 MediaCrawler 的 User-Agent 随机化策略:
  - MediaCrawler 使用 get_user_agent() / get_mobile_user_agent() 工具函数
  - 每次请求/上下文使用不同的 UA 来降低被指纹识别封禁的概率
  - 包含桌面端和移动端两大类别
"""

from __future__ import annotations

import random


# ── Desktop Windows Chrome User-Agents ────────────────────────────────
_DESKTOP_WINDOWS_CHROME: tuple[str, ...] = (
    # Chrome 120-131 on Windows 10/11
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

# ── Desktop Windows Edge User-Agents ──────────────────────────────────
_DESKTOP_WINDOWS_EDGE: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
)

# ── Desktop macOS Chrome User-Agents ──────────────────────────────────
_DESKTOP_MACOS_CHROME: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)

# ── Desktop macOS Safari User-Agents ──────────────────────────────────
_DESKTOP_MACOS_SAFARI: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
)

# ── Mobile iOS Safari User-Agents ─────────────────────────────────────
_MOBILE_IOS_SAFARI: tuple[str, ...] = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.7 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
)

# ── Mobile Android Chrome User-Agents ─────────────────────────────────
_MOBILE_ANDROID_CHROME: tuple[str, ...] = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 9 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.104 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.104 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.86 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.100 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Xiaomi 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.99 Mobile Safari/537.36",
)

# ── Combined pool ─────────────────────────────────────────────────────
_ALL_DESKTOP: tuple[str, ...] = (
    *_DESKTOP_WINDOWS_CHROME,
    *_DESKTOP_WINDOWS_EDGE,
    *_DESKTOP_MACOS_CHROME,
    *_DESKTOP_MACOS_SAFARI,
)

_ALL_MOBILE: tuple[str, ...] = (
    *_MOBILE_IOS_SAFARI,
    *_MOBILE_ANDROID_CHROME,
)

_ALL_USER_AGENTS: tuple[str, ...] = (*_ALL_DESKTOP, *_ALL_MOBILE)


class UserAgentManager:
    """Provides random User-Agent strings for browser context rotation.

    Usage:
        ua_mgr = UserAgentManager()
        desktop_ua = ua_mgr.random()           # any UA
        win_ua = ua_mgr.random_windows()        # Windows Chrome only
        mobile_ua = ua_mgr.random_mobile()      # mobile only
    """

    def random(self) -> str:
        """Return a random User-Agent from the full pool."""
        return random.choice(_ALL_USER_AGENTS)

    def random_desktop(self) -> str:
        """Return a random desktop User-Agent."""
        return random.choice(_ALL_DESKTOP)

    def random_mobile(self) -> str:
        """Return a random mobile User-Agent."""
        return random.choice(_ALL_MOBILE)

    def random_windows(self) -> str:
        """Return a random Windows Chrome/Edge User-Agent."""
        pool = (*_DESKTOP_WINDOWS_CHROME, *_DESKTOP_WINDOWS_EDGE)
        return random.choice(pool)

    def random_chrome(self) -> str:
        """Return a random Chrome (desktop) User-Agent."""
        return random.choice(_DESKTOP_WINDOWS_CHROME)
