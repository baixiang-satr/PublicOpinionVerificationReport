"""Small bounded cleanup helpers shared by browser lifecycle code."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse


async def close_quietly(resource: Any, timeout: float = 2.0) -> None:
    try:
        await asyncio.wait_for(resource.close(), timeout=timeout)
    except Exception:
        pass


def connection_closed(error: Exception) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "connection closed",
            "target closed",
            "browser has been closed",
            "pipe is being closed",
            "socket is closed",
        )
    )


def mask_proxy(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    if parsed.password:
        return proxy_url.replace(parsed.password, "****")
    return proxy_url
