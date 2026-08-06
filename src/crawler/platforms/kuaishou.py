"""Kuaishou short-video extractor with strict requested-photo matching.

Kuaishou's public share page hydrates from ``window.INIT_STATE`` and also
loads recommendation feeds.  Every feed item has the same caption/author/time
shape, so selecting the first ``photo`` silently assigns another creator's
work to the requested URL.  This extractor matches the compact or numeric
photo ID carried by the URL before accepting a node.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

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
from src.domain.models import ExtractionSource, PageData

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

_PATH_PHOTO_ID = re.compile(
    r"/(?:short-video|fw/photo)/([^/?#]+)",
    re.IGNORECASE,
)
_STRONG_PHOTO_ID = re.compile(r"[A-Za-z0-9_-]{6,}")

# INIT_STATE 水合常晚于导航稳定判定，同一条 URL 会时好时坏；有界轮询实时
# 页面兜底（网络载荷在导航结束即冻结，只有页面全局可以重读），轮询总量
# 必须短，由引擎 page_processing_timeout 最终兜底。
_HYDRATION_POLL_ATTEMPTS = 3
_HYDRATION_POLL_DELAY_MS = 900


class KuaishouExtractor:
    platform_keys = ("kuaishou",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        photo, source = await self._find_photo(page, document)
        if photo is None:
            return None
        user = photo.get("user")
        user = user if isinstance(user, Mapping) else {}
        internal_user_id = (
            text_at(photo, ("userId", "user_id", "authorId"))
            or text_at(user, ("user_id", "userId", "id", "authorId"))
        )
        profile_id = (
            text_at(photo, ("userEid", "user_eid", "eid"))
            or text_at(user, ("userEid", "user_eid", "eid"))
            or internal_user_id
        )
        public_account_id = (
            text_at(
                photo,
                ("kwaiId", "kwai_id", "kuaishouId", "kuaishou_id"),
            )
            or text_at(
                user,
                ("kwaiId", "kwai_id", "kuaishouId", "kuaishou_id"),
            )
            or profile_id
            or internal_user_id
        )
        caption = text_at(
            photo,
            ("caption", "title", "desc", "description"),
        )
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                # A short video has no separate semantic headline.  Mirroring
                # the caption prevents the generic shell title ("更多精彩…")
                # from becoming evidence.
                "title": caption,
                "content_text": caption,
                "author_name": (
                    text_at(photo, ("userName", "user_name", "name"))
                    or text_at(user, ("user_name", "userName", "name", "nickname"))
                ),
                # The template wants the public 快手号 (kwaiId), not the
                # numeric database userId used inside media payloads.
                "author_id": public_account_id,
                "author_url": (
                    f"https://www.kuaishou.com/profile/{profile_id}"
                    if profile_id
                    else None
                ),
                "published_at_dt": epoch_to_datetime(
                    epoch_at(photo, ("timestamp", "createTime", "uploadTime"))
                ),
            },
            source=source or ExtractionSource.EMBEDDED_JSON,
        )
        return data if applied and found_any(data, "content_text", "author_name") else None

    async def _find_photo(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> tuple[Mapping[str, Any] | None, ExtractionSource | None]:
        wanted_ids = _wanted_photo_ids(document.url)
        init_state = await evaluate_json(page, _INIT_STATE_SCRIPT)
        for payload, source in _ordered_payloads(init_state, document):
            photo = _photo_node(payload, wanted_ids=wanted_ids)
            if photo is not None:
                return photo, source
        for _ in range(_HYDRATION_POLL_ATTEMPTS):
            await _pause_between_polls(page)
            init_state = await evaluate_json(page, _INIT_STATE_SCRIPT)
            if init_state is None:
                continue
            photo = _photo_node(init_state, wanted_ids=wanted_ids)
            if photo is not None:
                return photo, ExtractionSource.EMBEDDED_JSON
        return None, None


def kuaishou_photo_id(url: str) -> str | None:
    """URL 锁定的目标 photo id；供未命中守卫判断是否剥离载荷字段。"""

    return next(iter(_wanted_photo_ids(url)), None)


def _ordered_payloads(
    init_state: Any,
    document: RenderedDocument,
) -> list[tuple[Any, ExtractionSource]]:
    payloads: list[tuple[Any, ExtractionSource]] = []
    if init_state is not None:
        payloads.append((init_state, ExtractionSource.EMBEDDED_JSON))
    payloads.extend(
        (payload, ExtractionSource.EMBEDDED_JSON)
        for payload in document.embedded_payloads
    )
    payloads.extend(
        (payload, ExtractionSource.NETWORK_JSON)
        for payload in document.network_payloads
    )
    return payloads


async def _pause_between_polls(page: Any) -> None:
    wait = getattr(page, "wait_for_timeout", None)
    if callable(wait):
        try:
            await wait(_HYDRATION_POLL_DELAY_MS)
            return
        except Exception:
            pass
    await asyncio.sleep(_HYDRATION_POLL_DELAY_MS / 1000)


def _photo_node(
    payload: Any,
    *,
    wanted_ids: frozenset[str] = frozenset(),
) -> Mapping[str, Any] | None:
    first_candidate: Mapping[str, Any] | None = None
    seen: set[int] = set()
    for mapping in iter_mappings(payload):
        photo = mapping.get("photo")
        candidates = (
            (photo, mapping)
            if isinstance(photo, Mapping)
            else (mapping,)
        )
        for candidate in candidates:
            marker = id(candidate)
            if marker in seen or not _looks_like_photo(candidate):
                continue
            seen.add(marker)
            if first_candidate is None:
                first_candidate = candidate
            if not wanted_ids or _mapping_mentions_id(candidate, wanted_ids):
                return candidate
    # When the URL identifies a photo, accepting an unmatched first item
    # would convert a recommendation into false evidence.
    return None if wanted_ids else first_candidate


def _looks_like_photo(node: Mapping[str, Any]) -> bool:
    return (
        ("caption" in node or "desc" in node or "description" in node)
        and (
            "timestamp" in node
            or "createTime" in node
            or "user" in node
            or "userName" in node
        )
    )


def _wanted_photo_ids(url: str) -> frozenset[str]:
    parsed = urlsplit(url)
    ids: set[str] = set()
    path_match = _PATH_PHOTO_ID.search(parsed.path)
    if path_match is not None:
        _add_photo_id(ids, path_match.group(1))
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("photoId", "photo_id", "shareObjectId"):
        for value in query.get(key, ()):
            _add_photo_id(ids, value)
    return frozenset(ids)


def _add_photo_id(ids: set[str], value: Any) -> None:
    candidate = unquote(str(value)).strip()
    if _STRONG_PHOTO_ID.fullmatch(candidate):
        ids.add(candidate)


def _mapping_mentions_id(
    node: Mapping[str, Any],
    wanted_ids: frozenset[str],
) -> bool:
    """Search one photo node, including its bounded URL/manifest children."""

    pending: list[Any] = [node]
    visited = 0
    while pending and visited < 1_000:
        value = pending.pop()
        visited += 1
        if isinstance(value, Mapping):
            pending.extend(value.values())
            continue
        if isinstance(value, (list, tuple)):
            pending.extend(value[:200])
            continue
        if value is None or isinstance(value, bool):
            continue
        text = str(value)
        if any(wanted_id in text for wanted_id in wanted_ids):
            return True
    return False


register(KuaishouExtractor())
