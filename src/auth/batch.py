"""Batch operations used by the authentication manager UI."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from threading import Event
from typing import Any

from src.auth.models import AuthProbeResult, AuthStatus, PlatformAuthPolicy
from src.auth.probe_helpers import ProgressCallback, _publish
from src.auth.store import AuthStateStoreError


async def probe_all_saved(
    service: Any,
    policies: Iterable[PlatformAuthPolicy],
    *,
    cancel_event: Event | None,
    on_progress: ProgressCallback | None,
) -> list[AuthProbeResult]:
    results: list[AuthProbeResult] = []
    for policy in policies:
        if cancel_event is not None and cancel_event.is_set():
            break
        results.append(
            await service.probe(
                policy.platform_key,
                use_saved_state=True,
                interactive=False,
                cancel_event=cancel_event,
                on_progress=on_progress,
            )
        )
    return results


async def login_all_missing(
    service: Any,
    policies: Iterable[PlatformAuthPolicy],
    *,
    cancel_event: Event | None,
    on_progress: ProgressCallback | None,
) -> list[AuthProbeResult]:
    """Establish missing profiles sequentially while skipping valid ones."""

    results: list[AuthProbeResult] = []
    for policy in policies:
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            if service._store.has_valid_state(policy.platform_key):
                profile = service._store.profile_for(policy.platform_key)
                result = AuthProbeResult(
                    platform_key=policy.platform_key,
                    status=AuthStatus.VALID,
                    checked_at=datetime.now().astimezone(),
                    original_url=policy.probe_url,
                    final_url=profile.validation_url,
                    message="已存在有效登录态，本次首次登录流程已跳过。",
                    used_saved_state=True,
                )
                _publish(
                    on_progress,
                    policy.platform_key,
                    result.status,
                    result.message,
                )
                results.append(result)
                continue
        except (AuthStateStoreError, KeyError):
            # The regular flow records a precise error or replaces an
            # unreadable state instead of aborting the entire batch.
            pass
        results.append(
            await service.probe(
                policy.platform_key,
                use_saved_state=True,
                interactive=True,
                cancel_event=cancel_event,
                on_progress=on_progress,
            )
        )
    return results
