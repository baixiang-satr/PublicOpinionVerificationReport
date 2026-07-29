"""Xiaohongshu (RED) dedicated extractor.

Note pages hydrate via ``window.__INITIAL_STATE__`` (already collected by the
generic document script).  The note detail lives under
``note.noteDetailMap[<id>].note`` with title/desc/time/user.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    epoch_to_datetime,
    found_any,
)
from src.crawler.platforms.payload_search import epoch_at, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import PageData


class XiaohongshuExtractor:
    platform_keys = ("xiaohongshu",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        note = self._find_note(document)
        if note is None:
            return None
        user = note.get("user")
        user = user if isinstance(user, Mapping) else {}
        user_id = text_at(user, ("userId", "user_id", "id"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": text_at(note, ("title",)),
                "content_text": text_at(note, ("desc", "content")),
                "author_name": text_at(user, ("nickname", "nickName", "name")),
                "author_id": user_id,
                "author_url": (
                    f"https://www.xiaohongshu.com/user/profile/{user_id}"
                    if user_id
                    else None
                ),
                "published_at_dt": epoch_to_datetime(
                    epoch_at(note, ("time", "publishTime", "createTime"))
                ),
            },
        )
        return data if applied and found_any(data, "content_text", "title") else None

    def _find_note(self, document: RenderedDocument) -> Mapping[str, Any] | None:
        for payload in (*document.embedded_payloads, *document.network_payloads):
            note = _note_node(payload)
            if note is not None:
                return note
        return None


def _note_node(payload: Any) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(payload):
        detail_map = mapping.get("noteDetailMap")
        if isinstance(detail_map, Mapping):
            for entry in detail_map.values():
                if isinstance(entry, Mapping) and isinstance(entry.get("note"), Mapping):
                    return entry["note"]
        if "desc" in mapping and "user" in mapping and (
            "title" in mapping or "time" in mapping
        ):
            return mapping
    return None


register(XiaohongshuExtractor())
