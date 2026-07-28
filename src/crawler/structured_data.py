"""Extract auditable content fields from framework and API JSON payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import unescape
import re
from typing import Any

from src.domain.models import ExtractionSource, PageData


_TITLE_KEYS = (
    "headline",
    "title",
    "subject",
    "note_title",
    "noteTitle",
    "goods_name",
    "goodsName",
    "video_title",
    "videoTitle",
)
_CONTENT_KEYS = (
    "articleBody",
    "content_text",
    "contentText",
    "content",
    "description",
    "desc",
    "caption",
    "text",
    "summary",
    "dynamic",
)
_AUTHOR_NAME_KEYS = (
    "author_name",
    "authorName",
    "nickname",
    "nickName",
    "user_name",
    "userName",
    "username",
)
_AUTHOR_ID_KEYS = (
    "author_id",
    "authorId",
    "user_id",
    "userId",
    "uid",
    "sec_uid",
    "secUid",
)
_AUTHOR_URL_KEYS = (
    "author_url",
    "authorUrl",
    "profile_url",
    "profileUrl",
    "home_url",
    "homeUrl",
)
_PUBLISHED_KEYS = (
    "datePublished",
    "publish_time",
    "publishTime",
    "published_at",
    "publishedAt",
    "create_time",
    "createTime",
    "created_at",
    "createdAt",
    "timestamp",
)
_STORE_KEYS = (
    "store_name",
    "storeName",
    "shop_name",
    "shopName",
    "seller_name",
    "sellerName",
)
_ACCOUNT_KEYS = ("account_uin", "accountUin", "uin")
_IMAGE_KEYS = (
    "image",
    "images",
    "image_url",
    "imageUrl",
    "cover",
    "cover_url",
    "coverUrl",
    "thumbnailUrl",
)
_NESTED_AUTHOR_KEYS = ("author", "creator", "user", "owner", "account", "profile", "seller")
_CONTENT_SIGNAL_KEYS = frozenset(
    (*_TITLE_KEYS, *_CONTENT_KEYS, *_AUTHOR_NAME_KEYS, *_STORE_KEYS)
)


def payload_has_content(payload: Any) -> bool:
    """Return whether a bounded JSON walk contains likely content fields."""

    for node in _mapping_nodes(payload, max_nodes=500):
        for key in _CONTENT_SIGNAL_KEYS.intersection(node):
            if _text_value(node.get(key), max_chars=40_000):
                return True
    return False


class StructuredDataExtractor:
    """Merge the best content-shaped JSON node into a ``PageData`` object."""

    def apply(
        self,
        payloads: Iterable[Any],
        data: PageData,
        source: ExtractionSource,
    ) -> None:
        candidates = [
            (_candidate_score(node), node)
            for payload in payloads
            for node in _mapping_nodes(payload)
        ]
        for score, node in sorted(candidates, key=lambda item: item[0], reverse=True):
            if score <= 0:
                break
            self._apply_node(node, data, source)
            if data.title and data.content_text and (data.author_name or data.store_name):
                break

    def _apply_node(
        self,
        node: Mapping[str, Any],
        data: PageData,
        source: ExtractionSource,
    ) -> None:
        self._set(data, "title", _first_text(node, _TITLE_KEYS), source)
        self._set(data, "content_text", _first_text(node, _CONTENT_KEYS), source)
        self._set(data, "author_name", _first_text(node, _AUTHOR_NAME_KEYS), source)
        self._set(data, "author_id", _first_text(node, _AUTHOR_ID_KEYS), source)
        self._set(data, "author_url", _first_url(node, _AUTHOR_URL_KEYS), source)
        self._set(data, "published_at_raw", _first_text(node, _PUBLISHED_KEYS), source)
        self._set(data, "store_name", _first_text(node, _STORE_KEYS), source)
        self._set(data, "account_uin", _first_text(node, _ACCOUNT_KEYS), source)

        for nested_key in _NESTED_AUTHOR_KEYS:
            nested = node.get(nested_key)
            if not isinstance(nested, Mapping):
                continue
            self._set(
                data,
                "author_name",
                _first_text(nested, (*_AUTHOR_NAME_KEYS, "name", "displayName")),
                source,
            )
            self._set(
                data,
                "author_id",
                _first_text(nested, (*_AUTHOR_ID_KEYS, "id")),
                source,
            )
            self._set(
                data,
                "author_url",
                _first_url(nested, (*_AUTHOR_URL_KEYS, "url")),
                source,
            )
            if nested_key == "seller":
                self._set(
                    data,
                    "store_name",
                    _first_text(nested, (*_STORE_KEYS, "name")),
                    source,
                )

        for key in _IMAGE_KEYS:
            data.image_urls.extend(_image_urls(node.get(key)))

    @staticmethod
    def _set(
        data: PageData,
        field: str,
        value: str | None,
        source: ExtractionSource,
    ) -> None:
        if getattr(data, field) or not value:
            return
        setattr(data, field, value)
        data.field_sources[field] = source


def _mapping_nodes(
    payload: Any,
    *,
    max_nodes: int = 2_500,
    max_depth: int = 12,
) -> list[Mapping[str, Any]]:
    nodes: list[Mapping[str, Any]] = []
    pending: list[tuple[Any, int]] = [(payload, 0)]
    seen = 0
    while pending and seen < max_nodes:
        value, depth = pending.pop()
        seen += 1
        if depth > max_depth:
            continue
        if isinstance(value, Mapping):
            nodes.append(value)
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend((item, depth + 1) for item in value[:500])
    return nodes


def _candidate_score(node: Mapping[str, Any]) -> int:
    title = _first_text(node, _TITLE_KEYS)
    content = _first_text(node, _CONTENT_KEYS)
    author = _first_text(node, _AUTHOR_NAME_KEYS)
    if not author:
        for key in _NESTED_AUTHOR_KEYS:
            nested = node.get(key)
            if isinstance(nested, Mapping):
                author = _first_text(nested, (*_AUTHOR_NAME_KEYS, "name", "displayName"))
                if author:
                    break
    score = 0
    if title:
        score += 30 + min(len(title), 120) // 12
    if content:
        score += 50 + min(len(content), 2_000) // 40
    if author:
        score += 15
    if _first_text(node, _PUBLISHED_KEYS):
        score += 10
    if _first_text(node, _STORE_KEYS):
        score += 10
    return score


def _first_text(node: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = _text_value(node.get(key))
        if value:
            return value
    return None


def _first_url(node: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, Mapping):
            value = value.get("url") or value.get("href")
        text = _text_value(value, max_chars=4_096)
        if text and text.startswith(("http://", "https://", "/")):
            return text
    return None


def _text_value(value: Any, *, max_chars: int = 100_000) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > max_chars:
        return None
    if "<" in text and ">" in text:
        text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _image_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(value, Mapping):
        return _image_urls(
            value.get("url")
            or value.get("src")
            or value.get("contentUrl")
            or value.get("uri")
        )
    if isinstance(value, (list, tuple)):
        return [url for item in value[:100] for url in _image_urls(item)]
    return []
