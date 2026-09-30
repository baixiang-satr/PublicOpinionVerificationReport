"""引擎 LLM 失效判定：灰区页面删除文案的"逐字引用"探测。

只在规则屏障未确证、且正文极短/空壳（疑似软 404）时才调用大模型；
设置未启用/不完整、网络或解析失败一律静默降级为不判定，绝不影响抓取
主流程。模型引文必须通过原文子串校验（忽略空白）才采信。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from src.config.settings import TaskConfig
from src.domain.models import PageData
from src.llm.client import LlmClient
from src.llm.deletion import (
    build_deletion_prompt,
    parse_deletion_response,
    verify_deletion_quote,
)
from src.llm.extract import source_text
from src.llm.models import LlmSettings
from src.llm.store import LlmSettingsStore, default_llm_settings_store

logger = logging.getLogger(__name__)

#: 灰区门控：可见正文不超过该长度才疑似软 404（正常内容页面绝不调用 LLM）。
#: 短微博类极短正常正文可能因此多一次空判调用（模型应返回 null），实跑后可收紧。
_GRAY_ZONE_MAX_CONTENT_CHARS = 120


@dataclass(frozen=True)
class InvalidUrlCandidate:
    """抓取中判定的失效候选：引文为删除文案 marker/状态码描述/LLM 逐字引用。"""

    evidence_id: int
    url: str
    code: str
    citation: str


def is_gray_zone_page(page: PageData) -> bool:
    """规则未确证但正文极短/空壳的页面才值得 LLM 判定（疑似软 404）。"""

    content = (page.content_text or "").strip()
    return len(content) <= _GRAY_ZONE_MAX_CONTENT_CHARS


class LlmDeletionJudge:
    """Ask an OpenAI-compatible LLM for a verbatim deleted-content quote."""

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

    async def detect_deleted_quote(
        self,
        page: PageData,
        cancel_event: asyncio.Event | None,
    ) -> str | None:
        if cancel_event is not None and cancel_event.is_set():
            return None
        try:
            store = self._store or default_llm_settings_store()
            settings = store.load()
        except Exception as error:  # noqa: BLE001 - 设置损坏不阻断抓取
            logger.warning("LLM settings load failed, deletion judge skipped: %s", error)
            return None
        if not settings.is_complete() or not is_gray_zone_page(page):
            return None
        text = source_text(page, settings.max_input_chars)
        if not text.strip():
            return None
        factory = self._client_factory or (lambda value: LlmClient(value))
        client = factory(settings)
        system, user = build_deletion_prompt(page.final_url or "", text)
        try:
            raw = await asyncio.wait_for(
                client.complete(system, user),
                timeout=settings.timeout_seconds + 5,
            )
        except Exception as error:  # noqa: BLE001 - 模型/网络失败一律静默降级
            logger.warning("LLM deletion judge request failed: %s", error)
            return None
        if cancel_event is not None and cancel_event.is_set():
            return None
        quote = verify_deletion_quote(parse_deletion_response(raw), text)
        if quote:
            logger.info("LLM deletion judge confirmed deleted content: %r", quote)
        return quote


__all__ = ["InvalidUrlCandidate", "LlmDeletionJudge", "is_gray_zone_page"]
