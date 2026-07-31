"""Douyin dedicated extractor.

Douyin video pages hydrate from ``<script id="RENDER_DATA">`` whose content
is percent-encoded JSON.  The generic collector cannot parse it (it is not
valid raw JSON), so this extractor evaluates the script node directly and
walks the ``aweme.detail`` node.  Network payloads captured during the session
are searched with the same rules as a fallback.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
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
from src.utils.time_utils import parse_web_published_at

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
        detail, detail_source = await self._find_aweme_detail(page, document)
        if detail is None:
            return None
        author = detail.get("author")
        author = author if isinstance(author, Mapping) else {}
        sec_uid = text_at(author, ("sec_uid", "secUid"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                # Douyin videos do not expose a separate semantic title.  Use
                # the requested aweme's caption as the title too, so a noisy
                # hydration/config node cannot later supply an unrelated
                # "title" while TemplateRowMapper still avoids duplicating it
                # inside 信息内容.
                "title": text_at(detail, ("desc", "caption")),
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
            source=detail_source or ExtractionSource.EMBEDDED_JSON,
        )
        # The fixed template wants the time displayed on the content page.
        # Douyin's detail JSON keeps seconds, while the visible page normally
        # shows minute precision (e.g. 2026-07-29 17:56).  Preserve that
        # visible value and normalize the omitted seconds to :00.
        visible_time = _visible_published_at(document)
        visible_published = (
            parse_web_published_at(visible_time) if visible_time else None
        )
        if visible_published is not None:
            data.published_at_raw = visible_time
            data.published_at = visible_published
            data.field_sources["published_at_raw"] = ExtractionSource.PLATFORM_DOM
            data.field_sources["published_at"] = ExtractionSource.PLATFORM_DOM
            data.field_confidences["published_at_raw"] = 0.86
            data.field_confidences["published_at"] = 0.86
        return data if applied and found_any(data, "content_text", "author_name") else None

    async def _find_aweme_detail(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> tuple[Mapping[str, Any] | None, ExtractionSource | None]:
        render_data = await evaluate_json(page, _RENDER_DATA_SCRIPT)
        if render_data is not None:
            detail = _aweme_detail(
                render_data,
                wanted_id=douyin_aweme_id(document.url),
            )
            if detail is not None:
                return detail, ExtractionSource.EMBEDDED_JSON
        wanted_id = douyin_aweme_id(document.url)
        # Video pages also load detail payloads for *recommendations*; only
        # the node matching the requested aweme id is truthful evidence.
        # When the URL carries an id and no node matches, return None and
        # let the generic DOM extraction describe the *visible* page instead
        # of pinning a recommendation's fields onto this URL.
        if wanted_id:
            for payload in document.network_payloads:
                detail = _aweme_detail(payload, wanted_id=wanted_id)
                if detail is not None:
                    return detail, ExtractionSource.NETWORK_JSON
            for payload in document.embedded_payloads:
                detail = _aweme_detail(payload, wanted_id=wanted_id)
                if detail is not None:
                    return detail, ExtractionSource.EMBEDDED_JSON
            detail = await douyin_aweme_detail(page, wanted_id)
            if detail is not None:
                return detail, ExtractionSource.NETWORK_JSON
            return None, None
        for payload, source in (
            *((item, ExtractionSource.NETWORK_JSON) for item in document.network_payloads),
            *((item, ExtractionSource.EMBEDDED_JSON) for item in document.embedded_payloads),
        ):
            detail = _aweme_detail(payload)
            if detail is not None:
                return detail, source
        return None, None


def _aweme_detail(
    payload: Any,
    wanted_id: str | None = None,
) -> Mapping[str, Any] | None:
    # Current web detail responses use:
    #   {"aweme_detail": {"aweme_id": ..., "desc": ..., "create_time": ...}}
    # This direct shape must be checked before the older RENDER_DATA
    # ``aweme.detail`` shape.  Missing it caused the generic extractor to
    # select an unrelated "厂牌排名规则" configuration node.
    if isinstance(payload, Mapping):
        direct = payload.get("aweme_detail")
        if isinstance(direct, Mapping) and _looks_like_aweme(direct):
            if _id_matches(direct, wanted_id):
                return direct
    holder = find_mapping_with(payload, ("aweme",))
    if holder is not None:
        aweme = holder.get("aweme")
        if isinstance(aweme, Mapping):
            detail = aweme.get("detail")
            if isinstance(detail, Mapping) and _looks_like_aweme(detail):
                if _id_matches(detail, wanted_id):
                    return detail
    for mapping in iter_mappings(payload):
        if _looks_like_aweme(mapping):
            if _id_matches(mapping, wanted_id):
                return mapping
    return None


def _looks_like_aweme(node: Mapping[str, Any]) -> bool:
    """Require content+author and either an id or a plausible create time."""

    return (
        ("desc" in node or "caption" in node)
        and "author" in node
        and (
            "aweme_id" in node
            or "awemeId" in node
            or "create_time" in node
            or "createTime" in node
        )
    )


def _visible_published_at(document: RenderedDocument) -> str | None:
    selected = (
        document.platform_values.get("published_at")
        or document.platform_values.get("published_at_raw")
    )
    if selected:
        return selected
    # Current Douyin video DOM no longer consistently exposes the old
    # data-e2e attribute, but its rendered evidence text includes a labelled
    # value such as 「发布时间：2026-07-29 17:56」.  Require that label so dates
    # from recommended cards cannot be mistaken for the target video.
    match = re.search(
        r"(?:发布时间|发布于)\s*[:：]?\s*"
        r"((?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
        r"\s+\d{1,2}:\d{2}(?::\d{2})?)",
        document.visible_text,
    )
    return match.group(1) if match else None


def _id_matches(node: Mapping[str, Any], wanted_id: str | None) -> bool:
    if not wanted_id:
        return True
    candidate = node.get("aweme_id") or node.get("awemeId")
    if candidate is None and isinstance(node.get("statistics"), Mapping):
        statistics = node["statistics"]
        candidate = statistics.get("aweme_id") or statistics.get("awemeId")
    return candidate is not None and str(candidate) == wanted_id


register(DouyinExtractor())
