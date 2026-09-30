"""OpenAI 兼容 chat/completions 异步客户端（httpx）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.llm.models import LlmSettings

logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """Raised when the LLM endpoint cannot complete a request."""


class LlmClient:
    """Minimal OpenAI-compatible client; instance semaphore bounds parallelism."""

    def __init__(
        self,
        settings: LlmSettings,
        *,
        max_parallel: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def complete(self, system: str, user: str) -> str:
        async with self._semaphore:
            return await self._request(system, user)

    async def _request(self, system: str, user: str) -> str:
        settings = self._settings
        url = f"{settings.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": settings.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {settings.api_key}"}
        try:
            async with httpx.AsyncClient(
                timeout=settings.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as error:
            raise LlmError(f"请求超时（{settings.timeout_seconds:.0f} 秒）") from error
        except httpx.HTTPError as error:
            raise LlmError(f"网络错误：{error}") from error
        if response.status_code != 200:
            detail = response.text[:200].strip()
            raise LlmError(f"接口返回 HTTP {response.status_code}：{detail}")
        try:
            data: Any = response.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LlmError("响应不是有效的 chat/completions 格式。") from error
        text = str(content).strip()
        if not text:
            raise LlmError("模型返回了空内容。")
        return text
