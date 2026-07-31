"""ByteDance SSR extractors: Toutiao (articles) and Xigua (videos).

Both apps hydrate from ``window._SSR_HYDRATED_DATA`` which the generic
collector already captures into ``embedded_payloads``.  The two platforms use
different node shapes, so each extractor walks the payload with its own
rules; this module only shares the lookup plumbing.
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

_SSR_SCRIPT = """
() => {
  try {
    const state = window._SSR_HYDRATED_DATA || window.__SSR_DATA__;
    return state ? JSON.stringify(state) : null;
  } catch (error) {
    return null;
  }
}
"""


class _SsrPayloadMixin:
    async def _payloads(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> list[Any]:
        state = await evaluate_json(page, _SSR_SCRIPT)
        payloads: list[Any] = []
        if state is not None:
            payloads.append(state)
        payloads.extend(document.embedded_payloads)
        payloads.extend(document.network_payloads)
        return payloads


class ToutiaoExtractor(_SsrPayloadMixin):
    platform_keys = ("toutiao",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        for payload in await self._payloads(page, document):
            node = _toutiao_article(payload)
            if node is None:
                continue
            media = (
                node.get("mediaInfo")
                or node.get("media_info")
                or node.get("media")
                or node.get("author")
                or node.get("userInfo")
                or node.get("user")
                or {}
            )
            media = media if isinstance(media, Mapping) else {}
            published_raw = text_at(node, ("publishTime", "publish_time"))
            data = PageData(final_url=document.url)
            applied = apply_json_fields(
                data,
                {
                    "title": text_at(node, ("title",)),
                    "content_text": _html_or_text(text_at(node, ("content", "abstract"))),
                    "author_name": text_at(media, ("name", "nickname"))
                    or text_at(node, ("source", "media_name", "mediaName")),
                    # Toutiao payload variants use both user and media IDs for
                    # the public account. The opaque /token/... route is not
                    # exported as an account: if none of these facts exists,
                    # the shared author finalizer deliberately uses nickname.
                    "author_id": text_at(
                        media,
                        (
                            "user_id",
                            "userId",
                            "media_id",
                            "mediaId",
                            "uid",
                            "id",
                        ),
                    ),
                    "author_url": text_at(
                        media,
                        (
                            "url",
                            "user_url",
                            "userUrl",
                            "home_url",
                            "homeUrl",
                        ),
                    )
                    or text_at(node, ("source_url", "sourceUrl")),
                    "published_at_raw": published_raw,
                    "published_at_dt": epoch_to_datetime(
                        epoch_at(node, ("publishTime", "publish_time"))
                    )
                    or (parse_web_published_at(published_raw) if published_raw else None),
                },
            )
            if applied and found_any(data, "content_text", "title"):
                return data
        return None


class XiguaExtractor(_SsrPayloadMixin):
    platform_keys = ("ixigua",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        for payload in await self._payloads(page, document):
            node = _xigua_video(payload)
            if node is None:
                continue
            user = node.get("userInfo") or node.get("user") or {}
            user = user if isinstance(user, Mapping) else {}
            user_id = text_at(user, ("user_id", "userId", "id"))
            published_raw = text_at(node, ("publish_time", "publishTime"))
            data = PageData(final_url=document.url)
            applied = apply_json_fields(
                data,
                {
                    "title": text_at(node, ("title",)),
                    "content_text": text_at(node, ("abstract", "desc", "video_abstract"))
                    or text_at(node, ("title",)),
                    "author_name": text_at(user, ("name", "nickname")),
                    "author_id": user_id,
                    "author_url": (
                        f"https://www.ixigua.com/home/{user_id}" if user_id else None
                    ),
                    "published_at_raw": published_raw,
                    "published_at_dt": epoch_to_datetime(
                        epoch_at(node, ("publish_time", "publishTime"))
                    )
                    or (parse_web_published_at(published_raw) if published_raw else None),
                },
            )
            if applied and found_any(data, "content_text", "title"):
                return data
        return None


def _toutiao_article(payload: Any) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(payload):
        info = mapping.get("articleInfo")
        if isinstance(info, Mapping) and ("title" in info or "content" in info):
            return info
        if "title" in mapping and "content" in mapping and (
            "publishTime" in mapping or "mediaInfo" in mapping
        ):
            return mapping
    return None


def _xigua_video(payload: Any) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(payload):
        if "title" in mapping and (
            "userInfo" in mapping or "publish_time" in mapping
        ):
            return mapping
    return None


def _html_or_text(raw: str | None) -> str | None:
    if not raw:
        return None
    if "<" in raw and ">" in raw:
        return strip_html(raw)
    return raw


register(ToutiaoExtractor())
register(XiguaExtractor())
