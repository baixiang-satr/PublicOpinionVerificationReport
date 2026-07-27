"""Per-host cooperative rate limiting with cancellation support."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import time
from urllib.parse import urlsplit


class HostRateLimiter:
    def __init__(self, minimum_interval_seconds: float) -> None:
        self._interval = minimum_interval_seconds
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_allowed: dict[str, float] = {}

    async def wait(self, url: str, cancel_event: asyncio.Event | None = None) -> None:
        host = (urlsplit(url).hostname or "").lower()
        async with self._locks[host]:
            delay = max(0.0, self._next_allowed.get(host, 0.0) - time.monotonic())
            if delay:
                await wait_with_cancellation(delay, cancel_event)
            self._next_allowed[host] = time.monotonic() + self._interval


async def wait_with_cancellation(seconds: float, cancel_event: asyncio.Event | None) -> None:
    if seconds <= 0:
        return
    if cancel_event is None:
        await asyncio.sleep(seconds)
        return
    if cancel_event.is_set():
        raise asyncio.CancelledError
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
    except TimeoutError:
        return
    raise asyncio.CancelledError
