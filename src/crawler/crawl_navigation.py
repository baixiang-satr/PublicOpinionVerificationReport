"""One-page navigation with access checks, official URL fallbacks and JSON capture."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from src.config.settings import TaskConfig
from src.crawler.navigation import (
    navigate_page,
    stabilize_rendered_page,
    wait_out_challenge_interstitial,
)
from src.crawler.network_payloads import NetworkPayloadCollector
from src.crawler.platform_fallbacks import (
    navigation_candidates,
    should_try_next_candidate,
)
from src.crawler.platform_router import PlatformRouter
from src.crawler.platform_types import PlatformDefinition
from src.crawler.share_links import resolve_share_link
from src.domain.models import RecordStatus, TaskError
from src.tools.page_access import (
    inspect_http_response,
    inspect_page_access,
    wait_for_manual_access,
)


@dataclass
class CrawlFailure(Exception):
    error: TaskError
    status: RecordStatus


@dataclass(frozen=True)
class NavigationOutcome:
    final_url: str
    status_code: int | None
    redirect_chain: list[str]
    definition: PlatformDefinition | None
    network_payloads: tuple[Any, ...]
    warnings: tuple[TaskError, ...] = ()


async def navigate_with_fallback(
    page: Any,
    original_url: str,
    config: TaskConfig,
    router: PlatformRouter,
    browser_pool: Any,
    cancel_event: asyncio.Event,
) -> NavigationOutcome:
    collector = NetworkPayloadCollector(
        enabled=config.capture_network_json,
        max_payload_bytes=config.max_structured_payload_bytes,
        max_payloads=config.max_structured_payloads,
    )
    original_definition = router.definition_for(original_url)
    candidates = (
        navigation_candidates(original_url, original_definition)
        if config.enable_platform_fallbacks
        else (original_url,)
    )
    warnings: list[TaskError] = []

    # Share short-links (v.douyin.com …) are resolved to their canonical
    # content URL up front: cheaper than a blind render and it exposes the
    # platform's content id for the API-assist fallbacks.
    pre_hops: list[str] = []
    if candidates:
        resolved = await resolve_share_link(page, candidates[0])
        if resolved and resolved != candidates[0]:
            pre_hops = [candidates[0], resolved]
            candidates = (resolved, *candidates[1:])
    # Attach only after short-link resolution: the canonical URL carries
    # the content id used for priority payload retention.
    collector.attach(page, candidates[0] if candidates else original_url)

    for candidate_index, candidate_url in enumerate(candidates):
        response, partial_error = await navigate_page(
            page,
            candidate_url,
            config.page_timeout_seconds * 1000,
            cancel_event,
        )
        if partial_error is not None:
            warnings.append(partial_error)
        final_url = str(page.url)
        status_code = int(response.status) if response is not None else None
        redirect_chain = _redirect_chain(response, original_url, final_url)
        for hop in reversed(pre_hops[:-1]):
            if hop not in redirect_chain:
                redirect_chain.insert(0, hop)
        try:
            _check_response(status_code)
            _raise_if_cancelled(cancel_event)
            definition = router.definition_for(final_url) or original_definition
            await wait_out_challenge_interstitial(page)
            await stabilize_rendered_page(
                page,
                config.page_stabilize_milliseconds,
                definition=definition,
            )
            _raise_if_cancelled(cancel_event)
            barrier = await inspect_page_access(page, final_url, original_url)
            if (
                barrier is not None
                and barrier.manual_recoverable
                and not config.headless
                and config.manual_intervention_timeout_seconds
            ):
                barrier = await wait_for_manual_access(
                    page,
                    final_url,
                    original_url,
                    config.manual_intervention_timeout_seconds,
                    cancel_event=cancel_event,
                )
                final_url = str(page.url)
                if final_url and redirect_chain[-1] != final_url:
                    redirect_chain.append(final_url)
                definition = router.definition_for(final_url) or original_definition
                await stabilize_rendered_page(page, 0, definition=definition)
            if barrier is not None:
                raise CrawlFailure(
                    TaskError(
                        "access",
                        barrier.code,
                        barrier.message,
                        retryable=barrier.retryable,
                    ),
                    barrier.status,
                )
        except CrawlFailure as failure:
            has_next = candidate_index + 1 < len(candidates)
            if has_next and should_try_next_candidate(failure.error.code):
                continue
            browser_pool.mark_access_invalid(
                page,
                original_url,
                barrier_code=failure.error.code,
                message=failure.error.message,
            )
            raise

        if candidate_index:
            warnings.append(
                TaskError(
                    "navigation",
                    "PLATFORM_FALLBACK_USED",
                    (
                        "原始内容页不可用，已改用同平台官方页面变体完成采集："
                        f"{candidate_url}"
                    ),
                    retryable=False,
                )
            )
        return NavigationOutcome(
            final_url=final_url,
            status_code=status_code,
            redirect_chain=redirect_chain,
            definition=definition,
            network_payloads=await collector.finish(page),
            warnings=tuple(warnings),
        )

    raise CrawlFailure(
        TaskError(
            "navigation",
            "NAVIGATION_FAILED",
            "所有同平台官方页面候选均不可用。",
            retryable=True,
        ),
        RecordStatus.FAILED,
    )


def _check_response(status_code: int | None) -> None:
    barrier = inspect_http_response(status_code)
    if barrier is not None:
        raise CrawlFailure(
            TaskError(
                "navigation",
                barrier.code,
                barrier.message,
                barrier.retryable,
            ),
            barrier.status,
        )


def _redirect_chain(
    response: Any,
    original_url: str,
    final_url: str,
) -> list[str]:
    urls: list[str] = []
    request = response.request if response is not None else None
    while request is not None:
        urls.append(str(request.url))
        request = request.redirected_from
    urls.reverse()
    if not urls:
        urls.append(original_url)
    if final_url and urls[-1] != final_url:
        urls.append(final_url)
    return list(dict.fromkeys(urls))


def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
    if cancel_event.is_set():
        raise asyncio.CancelledError
