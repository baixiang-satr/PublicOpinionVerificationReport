"""Bounded collection of content-shaped XHR/fetch JSON observed by Playwright."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.crawler.structured_data import payload_has_content


logger = logging.getLogger(__name__)


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
        self._pending: set[asyncio.Task[None]] = set()
        self._listener: Any = None

    def attach(self, page: Any) -> None:
        if not self._enabled or not hasattr(page, "on"):
            return

        def listener(response: Any) -> None:
            if len(self._payloads) >= self._max_payloads:
                return
            task = asyncio.create_task(self._capture(response))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        self._listener = listener
        page.on("response", listener)

    async def finish(self, page: Any | None = None) -> tuple[Any, ...]:
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        if page is not None and self._listener is not None and hasattr(page, "remove_listener"):
            try:
                page.remove_listener("response", self._listener)
            except Exception:
                pass
        return tuple(self._payloads)

    async def _capture(self, response: Any) -> None:
        if len(self._payloads) >= self._max_payloads:
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
            if payload_has_content(payload):
                self._payloads.append(payload)
        except Exception as error:
            logger.debug("Ignoring unreadable JSON response: %s", error)
