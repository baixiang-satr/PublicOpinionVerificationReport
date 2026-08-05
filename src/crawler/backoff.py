"""指数退避 + 全抖动（AWS 推荐策略），比固定退避更难被风控检测。"""

from __future__ import annotations

import asyncio
import logging
import random

from src.crawler.rate_limiter import wait_with_cancellation

logger = logging.getLogger(__name__)


async def backoff(
    base_delay_seconds: float,
    attempt: int,
    cancel_event: asyncio.Event,
) -> None:
    base = base_delay_seconds * (2**attempt)
    cap = min(base, 30.0)  # 上限 30 秒
    sleep = random.uniform(0, cap)
    logger.info(
        "Backoff attempt %d: sleeping %.1fs (base=%.1f)",
        attempt + 1,
        sleep,
        base,
    )
    await wait_with_cancellation(sleep, cancel_event)
