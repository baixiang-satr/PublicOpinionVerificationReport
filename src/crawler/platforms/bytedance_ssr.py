"""ByteDance SSR extractors: Toutiao (articles + 微头条) and Xigua (videos).

Both apps historically hydrated from ``window._SSR_HYDRATED_DATA``.  Current
pages may instead expose ``__SSR_DATA__`` / ``__INITIAL_STATE__`` /
``__NEXT_DATA__`` / percent-encoded ``#RENDER_DATA`` script nodes, so the
probe tries every known hydration surface in order.  The generic collector
also captures raw JSON into ``embedded_payloads``/``network_payloads``.
Every node lookup is locked to the content id carried by the page URL so
recommendation nodes can never supply fields for the target page.
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
    strip_html,
)
from src.crawler.platforms.payload_search import epoch_at, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import PageData
from src.utils.time_utils import parse_web_published_at

_SSR_SCRIPT = """
() => {
  const pick = (value) => {
    try { return value ? JSON.stringify(value) : null; } catch (error) { return null; }
  };
  const w = window;
  for (const key of ['_SSR_HYDRATED_DATA', '__SSR_DATA__', '__INITIAL_STATE__', '__MODERN_DATA__']) {
    const text = pick(w[key]);
    if (text) return text;
  }
  const render = document.getElementById('RENDER_DATA');
  if (render) {
    try { return decodeURIComponent(render.textContent || ''); } catch (error) { return null; }
  }
  const next = document.getElementById('__NEXT_DATA__');
  return next ? (next.textContent || null) : null;
}
"""

_XIGUA_ID_RE = re.compile(r"/(?:video|dx)/(\d{10,})")
_TOUTIAO_W_RE = re.compile(r"/w/(\d{6,})")
_TOUTIAO_ARTICLE_RE = re.compile(r"/article/(\d{6,})")

# Keys under which bytedance payloads store the content object's own id.
_NODE_ID_KEYS = (
    "group_id",
    "groupId",
    "item_id",
    "itemId",
    "video_id",
    "videoId",
    "id",
    "gid",
)


def _ixigua_group_id(url: str) -> str | None:
    match = _XIGUA_ID_RE.search(url)
    return match.group(1) if match else None


def _toutiao_content_id(url: str) -> str | None:
    for pattern in (_TOUTIAO_ARTICLE_RE, _TOUTIAO_W_RE):
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _node_id_values(node: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in _NODE_ID_KEYS:
        value = node.get(key)
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if text.isdigit():
            values.add(text)
    return values


def _id_locked(node: Mapping[str, Any], wanted_id: str | None) -> bool:
    """True when the node provably describes the URL's content object."""

    if not wanted_id:
        return True
    ids = _node_id_values(node)
    if not ids:
        return False
    return wanted_id in ids


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
        wanted_id = _toutiao_content_id(document.url)
        is_weitoutiao = _TOUTIAO_W_RE.search(document.url) is not None
        for payload in await self._payloads(page, document):
            node = _toutiao_article(payload, wanted_id=wanted_id)
            if node is None and is_weitoutiao:
                node = _toutiao_weitoutiao(payload, wanted_id=wanted_id)
            if node is None:
                continue
            media = (
                node.get("mediaInfo")
                or node.get("media_info")
                or node.get("media")
                or node.get("author")
                or node.get("userInfo")
                or node.get("user")
                or node.get("user_info")
                or {}
            )
            media = media if isinstance(media, Mapping) else {}
            published_raw = text_at(node, ("publishTime", "publish_time", "create_time", "createTime"))
            data = PageData(final_url=document.url)
            applied = apply_json_fields(
                data,
                {
                    "title": text_at(node, ("title",)),
                    "content_text": _html_or_text(text_at(node, ("content", "abstract", "text"))),
                    "author_name": text_at(media, ("name", "nickname", "screen_name"))
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
                        epoch_at(node, ("publishTime", "publish_time", "create_time", "createTime"))
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
        wanted_id = _ixigua_group_id(document.url)
        for payload in await self._payloads(page, document):
            node = _xigua_video(payload, wanted_id=wanted_id)
            if node is None:
                continue
            user = node.get("userInfo") or node.get("user") or node.get("user_info") or {}
            user = user if isinstance(user, Mapping) else {}
            user_id = text_at(user, ("user_id", "userId", "id", "uid"))
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


def _id_conflict(node: Mapping[str, Any], wanted_id: str | None) -> bool:
    """True when the node carries ids that provably belong to other content."""

    if not wanted_id:
        return False
    ids = _node_id_values(node)
    return bool(ids) and wanted_id not in ids


def _toutiao_article(
    payload: Any,
    *,
    wanted_id: str | None = None,
) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(payload):
        info = mapping.get("articleInfo")
        if isinstance(info, Mapping) and ("title" in info or "content" in info):
            if not _id_conflict(info, wanted_id):
                return info
            continue
        if "title" in mapping and "content" in mapping and (
            "publishTime" in mapping or "mediaInfo" in mapping
        ):
            if not _id_conflict(mapping, wanted_id):
                return mapping
    return None


def _toutiao_weitoutiao(
    payload: Any,
    *,
    wanted_id: str | None = None,
) -> Mapping[str, Any] | None:
    """微头条节点：短文本 + 用户对象，无语义标题。"""

    for mapping in iter_mappings(payload):
        if not any(key in mapping for key in ("content", "text")):
            continue
        user = (
            mapping.get("user")
            or mapping.get("userInfo")
            or mapping.get("user_info")
        )
        if not isinstance(user, Mapping):
            continue
        if _id_conflict(mapping, wanted_id):
            continue
        return mapping
    return None


def _xigua_video(
    payload: Any,
    *,
    wanted_id: str | None = None,
) -> Mapping[str, Any] | None:
    """Locate the target video node; recommendation nodes are rejected.

    When the URL exposes a group id, only nodes carrying that id in one of
    the known id keys qualify — a pageful of recommendation cards must never
    supply the fields of the requested video.
    """

    for mapping in iter_mappings(payload):
        if "title" not in mapping:
            continue
        if not any(
            key in mapping for key in ("userInfo", "user", "user_info", "publish_time", "publishTime")
        ):
            continue
        if wanted_id and not _id_locked(mapping, wanted_id):
            continue
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
