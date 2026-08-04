"""Bilibili dedicated extractor.

Video pages hydrate ``window.__INITIAL_STATE__`` whose ``videoData`` node
carries the authoritative fields (``bvid``/``aid``/``title``/``desc``/
``owner``/``pubdate``).  The extractor locks onto the node whose ``bvid``
(or ``aid``) matches the page URL — recommendation nodes inside the same
hydration payload belong to other videos and must never supply fields.
Falls back to ``None`` so the catalog DOM pipeline keeps its historically
good bilibili results whenever the hydration payload is unavailable.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
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
from src.domain.models import ExtractionSource, PageData

_INITIAL_STATE_SCRIPT = """
() => {
  try {
    return window.__INITIAL_STATE__ || null;
  } catch (error) {
    return null;
  }
}
"""

_BVID_RE = re.compile(r"/video/(BV[0-9A-Za-z]+)", re.I)
_AID_RE = re.compile(r"/video/av(\d+)", re.I)

# Hydration nodes that describe a concrete video carry these keys.
_VIDEO_SHAPE_KEYS = ("bvid", "aid", "title", "owner")


def _url_video_ids(url: str) -> tuple[str | None, str | None]:
    """Return (bvid, aid) recoverable from a bilibili video URL."""

    bvid_match = _BVID_RE.search(url)
    aid_match = _AID_RE.search(url)
    return (
        bvid_match.group(1) if bvid_match else None,
        aid_match.group(1) if aid_match else None,
    )


def _video_data(
    payload: Any,
    *,
    wanted_bvid: str | None,
    wanted_aid: str | None,
) -> Mapping[str, Any] | None:
    """Locate the video-detail node inside a hydration/JSON payload."""

    for node in iter_mappings(payload):
        if "title" not in node or "owner" not in node:
            continue
        if not isinstance(node.get("owner"), Mapping):
            continue
        if not any(key in node for key in ("bvid", "aid")):
            continue
        if wanted_bvid or wanted_aid:
            node_bvid = node.get("bvid")
            node_aid = node.get("aid")
            bvid_hit = (
                wanted_bvid
                and isinstance(node_bvid, str)
                and node_bvid.casefold() == wanted_bvid.casefold()
            )
            aid_hit = wanted_aid and str(node_aid or "").strip() == wanted_aid
            if not (bvid_hit or aid_hit):
                continue
        return node
    return None


class BilibiliExtractor:
    platform_keys = ("bilibili",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        wanted_bvid, wanted_aid = _url_video_ids(document.url)
        detail, detail_source = await self._find_video_data(
            page, document, wanted_bvid=wanted_bvid, wanted_aid=wanted_aid
        )
        if detail is None:
            return None
        owner = detail.get("owner")
        owner = owner if isinstance(owner, Mapping) else {}
        mid = text_at(owner, ("mid",))
        # B站视频没有独立摘要时简介即正文；简介为空时退回标题，
        # 与模板「无独立标题列的工作表标题入信息内容」约定一致。
        content = text_at(detail, ("desc",)) or text_at(detail, ("title",))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": text_at(detail, ("title",)),
                "content_text": content,
                "author_name": text_at(owner, ("name",)),
                "author_id": mid,
                "author_url": f"https://space.bilibili.com/{mid}" if mid else None,
                "published_at_dt": epoch_to_datetime(
                    epoch_at(detail, ("pubdate", "ctime", "create"))
                ),
            },
            source=detail_source or ExtractionSource.EMBEDDED_JSON,
        )
        return data if applied and found_any(data, "title", "author_name") else None

    async def _find_video_data(
        self,
        page: Any,
        document: RenderedDocument,
        *,
        wanted_bvid: str | None,
        wanted_aid: str | None,
    ) -> tuple[Mapping[str, Any] | None, ExtractionSource | None]:
        state = await evaluate_json(page, _INITIAL_STATE_SCRIPT)
        if state is not None:
            detail = _video_data(
                state, wanted_bvid=wanted_bvid, wanted_aid=wanted_aid
            )
            if detail is not None:
                return detail, ExtractionSource.EMBEDDED_JSON
        # ID 锁定铁律：URL 带 BV/av 号时，只有 ID 匹配的节点才是有效证据；
        # 推荐位节点再丰富也不能顶替。无匹配则交回目录 DOM 兜底。
        for payload in document.network_payloads:
            detail = _video_data(
                payload, wanted_bvid=wanted_bvid, wanted_aid=wanted_aid
            )
            if detail is not None:
                return detail, ExtractionSource.NETWORK_JSON
        for payload in document.embedded_payloads:
            detail = _video_data(
                payload, wanted_bvid=wanted_bvid, wanted_aid=wanted_aid
            )
            if detail is not None:
                return detail, ExtractionSource.EMBEDDED_JSON
        return None, None


register(BilibiliExtractor())
