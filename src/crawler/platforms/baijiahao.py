"""Baijiahao dedicated extractor.

Baijiahao article pages are SSR React apps that hydrate from
``window.__PRELOADED_STATE__`` (falling back to ``__INITIAL_STATE__``); the
article node carries title/content/author/publish_time.  DOM meta values
remain as generic fallbacks when the state is absent.
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
from src.crawler.platforms.payload_search import epoch_at, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import PageData
from src.utils.time_utils import parse_web_published_at

_STATE_SCRIPT = """
() => {
  try {
    const state = window.__PRELOADED_STATE__ || window.__INITIAL_STATE__;
    return state ? JSON.stringify(state) : null;
  } catch (error) {
    return null;
  }
}
"""

_TIME_KEYS = ("publish_time", "publishTime", "create_time", "created_at", "time")


class BaijiahaoExtractor:
    platform_keys = ("baijiahao",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        state = await evaluate_json(page, _STATE_SCRIPT)
        node = _article_node(state) if state is not None else None
        if node is None:
            return None
        author = node.get("author")
        author = author if isinstance(author, Mapping) else {}
        published_raw = text_at(node, _TIME_KEYS)
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": text_at(node, ("title",)),
                "content_text": _content_of(node),
                "author_name": text_at(author, ("name", "nickname"))
                or text_at(node, ("source", "author_name", "authorName")),
                "author_id": text_at(author, ("id", "uk"))
                or text_at(node, ("author_id", "authorId")),
                "author_url": text_at(author, ("home_url", "homeUrl", "url")),
                "published_at_raw": published_raw,
                "published_at_dt": epoch_to_datetime(epoch_at(node, _TIME_KEYS))
                or (parse_web_published_at(published_raw) if published_raw else None),
            },
        )
        return data if applied and found_any(data, "content_text", "title") else None


def _article_node(state: Any) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(state):
        if "title" in mapping and "content" in mapping and (
            "author" in mapping or "source" in mapping or "publish_time" in mapping
        ):
            return mapping
    return None


def _content_of(node: Mapping[str, Any]) -> str | None:
    raw = text_at(node, ("content", "article_content", "articleContent"))
    if not raw:
        return None
    if "<" in raw and ">" in raw:
        return strip_html(raw)
    return raw


register(BaijiahaoExtractor())
