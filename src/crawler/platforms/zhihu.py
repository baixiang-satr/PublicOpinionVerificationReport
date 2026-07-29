"""Zhihu dedicated extractor.

Zhihu SSR pages embed ``<script id="js-initialData" type="text/json">`` with
``initialState.entities`` holding questions, answers and articles.  The
generic collector does not pick that script id, so the extractor evaluates it
directly.  Works for ``/question/``, ``/answer/`` and ``zhuanlan /p/`` URLs.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    epoch_to_datetime,
    evaluate_json,
    found_any,
    strip_html,
)
from src.crawler.platforms.payload_search import epoch_at, text_at
from src.crawler.platforms.registry import register
from src.domain.models import PageData

_INITIAL_DATA_SCRIPT = """
() => {
  const node = document.getElementById('js-initialData');
  return node ? node.textContent : null;
}
"""


class ZhihuExtractor:
    platform_keys = ("zhihu",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        initial = await evaluate_json(page, _INITIAL_DATA_SCRIPT)
        if initial is None:
            return None
        entities = _entities(initial)
        if entities is None:
            return None
        data = PageData(final_url=document.url)
        applied = self._apply_entities(data, entities, document.url)
        return data if applied and found_any(data, "content_text", "title") else None

    def _apply_entities(
        self,
        data: PageData,
        entities: Mapping[str, Any],
        url: str,
    ) -> int:
        article = _first_entity(entities.get("articles"))
        if article is not None:
            return self._apply_node(
                data,
                title=text_at(article, ("title",)),
                content=text_at(article, ("content", "excerpt")),
                node=article,
                time_keys=("created", "updated", "createdTime"),
            )
        answer = _first_entity(entities.get("answers"))
        question = _first_entity(entities.get("questions"))
        if answer is not None:
            title = text_at(question or {}, ("title",))
            return self._apply_node(
                data,
                title=title,
                content=text_at(answer, ("content", "excerpt")),
                node=answer,
                time_keys=("createdTime", "updatedTime"),
            )
        if question is not None:
            return self._apply_node(
                data,
                title=text_at(question, ("title",)),
                content=text_at(question, ("detail", "excerpt")),
                node=question,
                time_keys=("created", "createdTime"),
            )
        return 0

    def _apply_node(
        self,
        data: PageData,
        *,
        title: str | None,
        content: str | None,
        node: Mapping[str, Any],
        time_keys: tuple[str, ...],
    ) -> int:
        author = node.get("author")
        author = author if isinstance(author, Mapping) else {}
        url_token = text_at(author, ("urlToken", "url_token"))
        return apply_json_fields(
            data,
            {
                "title": title,
                "content_text": strip_html(content) if content else None,
                "author_name": text_at(author, ("name",)),
                "author_id": text_at(author, ("id", "urlToken")),
                "author_url": (
                    f"https://www.zhihu.com/people/{url_token}" if url_token else None
                ),
                "published_at_dt": epoch_to_datetime(epoch_at(node, time_keys)),
            },
        )


def _entities(initial: Any) -> Mapping[str, Any] | None:
    if not isinstance(initial, Mapping):
        return None
    state = initial.get("initialState")
    if isinstance(state, Mapping) and isinstance(state.get("entities"), Mapping):
        return state["entities"]
    if isinstance(initial.get("entities"), Mapping):
        return initial["entities"]
    return None


def _first_entity(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    for entry in value.values():
        if isinstance(entry, Mapping):
            return entry
    return None


register(ZhihuExtractor())
