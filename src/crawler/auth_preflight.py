"""Authentication checks that run before any batch content navigation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from src.auth.registry import auth_policy_for_url
from src.config.settings import TaskConfig
from src.domain.models import RecordResult, TaskEvent, UrlTask

logger = logging.getLogger(__name__)


async def preflight_auth_profiles(
    config: TaskConfig,
    browser_pool: Any,
    auth_store: Any,
    queues: dict[str, list[UrlTask]],
    on_event: Callable[[TaskEvent], None] | None,
    emit: Callable[..., None],
    cancel_event: asyncio.Event,
) -> dict[str, bool]:
    """Fresh-context validation for every platform used by this batch."""

    if not config.enable_auth_health_gate:
        return {}
    revalidate = getattr(browser_pool, "revalidate_platform_profile", None)
    if not callable(revalidate) or auth_store is None:
        return {}
    semaphore = asyncio.Semaphore(config.max_concurrency)

    async def probe(queue: list[UrlTask]) -> tuple[str, bool] | None:
        if not queue or cancel_event.is_set():
            return None
        policy = auth_policy_for_url(queue[0].normalized_url)
        if policy is None:
            return None
        emit(
            RecordResult(task=queue[0]),
            "auth_preflight",
            f"正在抓取前复验 {policy.display_name} 登录态",
            on_event,
        )
        async with semaphore:
            try:
                valid = bool(await revalidate(policy.platform_key))
            except Exception as error:  # noqa: BLE001 - reported as failed preflight
                logger.warning("Auth preflight failed for %s: %s", policy.platform_key, error)
                valid = False
        return policy.platform_key, valid

    results = await asyncio.gather(*(probe(queue) for queue in queues.values()))
    return {key: valid for item in results if item for key, valid in (item,)}


async def preflight_or_empty(
    config: TaskConfig,
    browser_pool: Any,
    auth_store: Any,
    queues: dict[str, list[UrlTask]],
    on_event: Callable[[TaskEvent], None] | None,
    emit: Callable[..., None],
    cancel_event: asyncio.Event,
) -> dict[str, bool]:
    """Run auth preflight, degrading to an empty report when it fails."""

    try:
        return await preflight_auth_profiles(
            config,
            browser_pool,
            auth_store,
            queues,
            on_event,
            emit,
            cancel_event,
        )
    except Exception as error:  # noqa: BLE001 - never let preflight kill a batch
        logger.warning("Auth preflight failed, continuing without it: %s", error)
        return {}


async def try_heal_auth(browser_pool: Any, platform_key: str) -> bool:
    revalidate = getattr(browser_pool, "revalidate_platform_profile", None)
    if revalidate is None:
        return False
    try:
        return bool(await revalidate(platform_key))
    except Exception as error:  # noqa: BLE001 - authentication remains blocked
        logger.warning("Auth self-heal probe failed for %s: %s", platform_key, error)
        return False
