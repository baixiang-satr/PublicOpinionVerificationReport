"""Authentication and desktop-surface gates for evidence screenshots."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from src.auth.login_evidence import state_has_authenticated_session


class GuestCaptureError(RuntimeError):
    """Raised when a known authenticated platform is rendering as guest."""


async def require_authenticated_capture(page: Any, definition: Any) -> None:
    """Reject known guest contexts before an evidence image is written."""

    platform_key = str(getattr(definition, "key", "") or "")
    if not platform_key:
        return
    if platform_key == "douyin":
        host = (urlsplit(str(getattr(page, "url", "") or "")).hostname or "").lower()
        if host == "iesdouyin.com" or host.endswith(".iesdouyin.com"):
            raise GuestCaptureError(
                "Refusing a guest Douyin mobile-share page; desktop login state is required."
            )
    context = getattr(page, "context", None)
    if context is None or not hasattr(context, "storage_state"):
        return
    try:
        state = await context.storage_state(indexed_db=True)
    except TypeError:
        state = await context.storage_state()
    except Exception:
        return
    if state_has_authenticated_session(platform_key, state) is False:
        raise GuestCaptureError(
            f"Refusing guest capture for {platform_key}; authenticated session evidence is missing."
        )


async def douyin_desktop_surface_ready(page: Any) -> bool:
    """Accept a rendered desktop shell even when the video decoder is late."""

    if not hasattr(page, "evaluate"):
        return False
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const host = location.hostname.toLowerCase();
                    if (!(host === 'douyin.com' || host.endsWith('.douyin.com'))) return false;
                    if ((window.innerWidth || 0) < 900) return false;
                    const text = (document.body?.innerText || '').trim();
                    const surface = document.querySelector(
                      'video, [class*="player"], [data-e2e*="video"], main'
                    );
                    return Boolean(surface) && text.length >= 20;
                }"""
            )
        )
    except Exception:
        return False
