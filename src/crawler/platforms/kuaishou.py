"""Kuaishou dedicated extractor.

``kuaishou.com/short-video/<id>`` answers non-cookie browser requests with a
plain JSON body (``{"photo": {...}}``) which the generic pipeline flags as
UNEXPECTED_API_RESPONSE.  The JSON *is* the content, so this extractor parses
it directly; desktop HTML pages hydrate via ``window.INIT_STATE`` and are
walked with the same node rules.
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
)
from src.crawler.platforms.payload_search import epoch_at, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import PageData

_INIT_STATE_SCRIPT = """
() => {
  try {
    const state = window.INIT_STATE || window.__INITIAL_STATE__;
    return state ? JSON.stringify(state) : null;
  } catch (error) {
    return null;
  }
}
"""


class KuaishouExtractor:
    platform_keys = ("kuaishou",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        photo = await self._find_photo(page, document)
        if photo is None:
            return None
        user_id = text_at(photo, ("userId", "user_id", "authorId"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "content_text": text_at(photo, ("caption", "title")),
                "author_name": text_at(photo, ("userName", "user_name", "name")),
                "author_id": user_id,
                "author_url": (
                    f"https://www.kuaishou.com/profile/{user_id}" if user_id else None
                ),
                "published_at_dt": epoch_to_datetime(
                    epoch_at(photo, ("timestamp", "createTime", "uploadTime"))
                ),
            },
        )
        return data if applied and found_any(data, "content_text", "author_name") else None

    async def _find_photo(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> Mapping[str, Any] | None:
        init_state = await evaluate_json(page, _INIT_STATE_SCRIPT)
        payloads: list[Any] = []
        if init_state is not None:
            payloads.append(init_state)
        payloads.extend(document.embedded_payloads)
        payloads.extend(document.network_payloads)
        for payload in payloads:
            photo = _photo_node(payload)
            if photo is not None:
                return photo
        return None


def _photo_node(payload: Any) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(payload):
        photo = mapping.get("photo")
        if isinstance(photo, Mapping) and (
            "caption" in photo or "userName" in photo
        ):
            return photo
        if "caption" in mapping and "timestamp" in mapping:
            return mapping
    return None


register(KuaishouExtractor())
