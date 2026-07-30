"""Bounded collection of content-shaped XHR/fetch JSON observed by Playwright."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.crawler.platforms.payload_search import iter_mappings
from src.crawler.structured_data import candidate_scope, payload_has_content, url_content_ids


logger = logging.getLogger(__name__)

# Bounded drain for in-flight response bodies: long-lived streaming
# connections (e.g. douyin) must never block the navigation pipeline.
_FINISH_DRAIN_SECONDS = 4.0

# Payloads carrying the page URL's own content id (e.g. the requested
# douyin aweme detail) must survive the junk flood: recommendation/feed
# JSON fills the regular cap within the first second on video pages.
_MAX_PRIORITY_PAYLOADS = 8


class NetworkPayloadCollector:
    """Collect useful JSON responses without persisting cookies or raw traffic."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_payload_bytes: int = 2_000_000,
        max_payloads: int = 24,
    ) -> None:
        self._enabled = enabled
        self._max_payload_bytes = max_payload_bytes
        self._max_payloads = max_payloads
        self._payloads: list[Any] = []
        self._priority: list[Any] = []
        self._priority_ids: frozenset[str] = frozenset()
        self._pending: set[asyncio.Task[None]] = set()
        self._listener: Any = None

    def attach(self, page: Any, url: str | None = None) -> None:
        if not self._enabled or not hasattr(page, "on"):
            return
        if url:
            self._priority_ids = url_content_ids(url)

        def listener(response: Any) -> None:
            if len(self._payloads) >= self._max_payloads and not self._priority_slots_left():
                return
            task = asyncio.create_task(self._capture(response))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        self._listener = listener
        page.on("response", listener)

    def _priority_slots_left(self) -> bool:
        return bool(self._priority_ids) and len(self._priority) < _MAX_PRIORITY_PAYLOADS

    async def finish(self, page: Any | None = None) -> tuple[Any, ...]:
        # Detach first so no new capture tasks spawn while draining.
        if page is not None and self._listener is not None and hasattr(page, "remove_listener"):
            try:
                page.remove_listener("response", self._listener)
            except Exception:
                pass
        if self._pending:
            pending = tuple(self._pending)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=_FINISH_DRAIN_SECONDS,
                )
            except TimeoutError:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        # Priority payloads first: extractors must see the requested
        # content's node before any recommendation junk.
        return (*tuple(self._priority), *tuple(self._payloads))

    async def _capture(self, response: Any) -> None:
        over_cap = len(self._payloads) >= self._max_payloads
        if over_cap and not self._priority_slots_left():
            return
        try:
            status = int(getattr(response, "status", 0) or 0)
            if status < 200 or status >= 400:
                return
            request = getattr(response, "request", None)
            resource_type = str(getattr(request, "resource_type", "") or "").casefold()
            if resource_type and resource_type not in {"xhr", "fetch", "document"}:
                return
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("content-type") or "").casefold()
            if "json" not in content_type and not str(getattr(response, "url", "")).casefold().endswith(".json"):
                return
            content_length = int(headers.get("content-length") or 0)
            if content_length > self._max_payload_bytes:
                return
            body = await response.body()
            if not body or len(body) > self._max_payload_bytes:
                return
            payload = json.loads(body.decode("utf-8", errors="replace"))
            if not payload_has_content(payload):
                return
            if over_cap:
                if _payload_matches_ids(payload, self._priority_ids):
                    self._priority.append(payload)
                return
            self._payloads.append(payload)
        except Exception as error:
            logger.debug("Ignoring unreadable JSON response: %s", error)


def _payload_matches_ids(payload: Any, ids: frozenset[str]) -> bool:
    """Whether any node carries a strong content id from the page URL."""

    if not ids:
        return False
    for node in iter_mappings(payload, max_nodes=2_000):
        if candidate_scope(node, ids) == "main":
            return True
    return False
