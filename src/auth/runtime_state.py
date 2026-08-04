"""Authentication state transitions discovered during a crawl."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus


def expire_guest_ui_profile(
    store: Any,
    platform_key: str,
    original_url: str,
    message: str,
) -> None:
    """Downgrade a profile only after explicit rendered guest UI evidence."""

    store.record_result(
        AuthProbeResult(
            platform_key=platform_key,
            status=AuthStatus.EXPIRED,
            checked_at=datetime.now().astimezone(),
            original_url=original_url,
            barrier_code="LOGIN_UI_VISIBLE",
            message=message,
            used_saved_state=True,
        )
    )
