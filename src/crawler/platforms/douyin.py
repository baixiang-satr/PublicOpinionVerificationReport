"""Douyin dedicated extractor.

Douyin video pages hydrate from ``<script id="RENDER_DATA">`` whose content
is percent-encoded JSON.  The generic collector cannot parse it (it is not
valid raw JSON), so this extractor evaluates the script node directly and
walks the ``aweme.detail`` node.  Network payloads captured during the session
are searched with the same rules as a fallback.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

from src.crawler.api_assist import douyin_aweme_detail, douyin_aweme_id
from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    epoch_to_datetime,
    evaluate_json,
    found_any,
)
from src.crawler.platforms.payload_search import epoch_at, find_mapping_with, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import ExtractionSource, PageData

_RENDER_DATA_SCRIPT = """
() => {
  const node = document.getElementById('RENDER_DATA');
  return node ? decodeURIComponent(node.textContent || '') : null;
}
"""


class DouyinExtractor:
    platform_keys = ("douyin",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        detail, from_api = await self._find_aweme_detail(page, document)
        if detail is None:
            return None
        author = detail.get("author")
        author = author if isinstance(author, Mapping) else {}
        sec_uid = text_at(author, ("sec_uid", "secUid"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "content_text": text_at(detail, ("desc", "caption")),
                "author_name": text_at(author, ("nickname", "unique_id")),
                "author_id": text_at(author, ("unique_id", "short_id", "uid")),
                "author_url": (
                    f"https://www.douyin.com/user/{sec_uid}" if sec_uid else None
                ),
                "published_at_raw": None,
                "published_at_dt": epoch_to_datetime(
                    epoch_at(detail, ("createTime", "create_time"))
                ),
            },
            source=(
                ExtractionSource.NETWORK_JSON if from_api else ExtractionSource.EMBEDDED_JSON
            ),
        )
        return data if applied and found_any(data, "content_text", "author_name") else None

    async def _find_aweme_detail(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> tuple[Mapping[str, Any] | None, bool]:
        render_data = await evaluate_json(page, _RENDER_DATA_SCRIPT)
        payloads: list[Any] = []
        if render_data is not None:
            payloads.append(render_data)
        payloads.extend(document.network_payloads)
        payloads.extend(document.embedded_payloads)
        for payload in payloads:
            detail = _aweme_detail(payload)
            if detail is not None:
                return detail, False
        # Additive fallback: the render pipeline found nothing (login wall or
        # empty shell), so ask the public iesdouyin endpoints through the
        # live browser session.  The returned item node uses the same
        # desc/author keys the field mapping above already understands.
        aweme_id = douyin_aweme_id(document.url)
        if aweme_id:
            detail = await douyin_aweme_detail(page, aweme_id)
            if detail is not None:
                return detail, True
        return None, False


def _aweme_detail(payload: Any) -> Mapping[str, Any] | None:
    holder = find_mapping_with(payload, ("aweme",))
    if holder is not None:
        aweme = holder.get("aweme")
        if isinstance(aweme, Mapping):
            detail = aweme.get("detail")
            if isinstance(detail, Mapping) and ("desc" in detail or "author" in detail):
                return detail
    for mapping in iter_mappings(payload):
        if "desc" in mapping and "createTime" in mapping and "author" in mapping:
            return mapping
    return None


register(DouyinExtractor())
