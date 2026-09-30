"""引擎 LLM 兜底：字段缺失且配置可用时调用大模型从原文补全字段。

只有在设置页启用并填齐 base_url/model/Key 时才会真正发起请求；网络、
解析或设置异常一律只记日志、静默降级，绝不影响抓取主流程。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from src.config.settings import TaskConfig
from src.domain.models import PageData
from src.llm.client import LlmClient
from src.llm.extract import (
    apply_extraction,
    build_extraction_prompt,
    needs_fallback,
    parse_extraction_response,
    source_text,
)
from src.llm.store import LlmSettingsStore, default_llm_settings_store
from src.llm.models import LlmSettings

logger = logging.getLogger(__name__)


class LlmFallback:
    """Fill missing title/author/published_at via an OpenAI-compatible LLM."""

    def __init__(
        self,
        config: TaskConfig,
        *,
        store: LlmSettingsStore | None = None,
        client_factory: Callable[[LlmSettings], Any] | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._client_factory = client_factory

    async def fill_missing_fields(
        self,
        page: PageData,
        cancel_event: asyncio.Event | None,
    ) -> list[str]:
        if cancel_event is not None and cancel_event.is_set():
            return []
        try:
            store = self._store or default_llm_settings_store()
            settings = store.load()
        except Exception as error:  # noqa: BLE001 - 设置损坏不阻断抓取
            logger.warning("LLM settings load failed, fallback skipped: %s", error)
            return []
        if not settings.is_complete() or not needs_fallback(page):
            return []
        text = source_text(page, settings.max_input_chars)
        if not text.strip():
            return []
        factory = self._client_factory or (lambda value: LlmClient(value))
        client = factory(settings)
        system, user = build_extraction_prompt(page.final_url or "", text)
        try:
            raw = await asyncio.wait_for(
                client.complete(system, user),
                timeout=settings.timeout_seconds + 5,
            )
        except Exception as error:  # noqa: BLE001 - 模型/网络失败一律静默降级
            logger.warning("LLM fallback request failed: %s", error)
            return []
        filled = apply_extraction(page, parse_extraction_response(raw), text)
        if filled:
            logger.info("LLM fallback filled fields: %s", ",".join(filled))
        return filled
