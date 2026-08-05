"""Node classification and URL-target matching helpers for Baijiahao.

Split from ``baijiahao.py`` to keep each module under the 500-line limit.
Everything here is pure payload-walking logic with no page or protocol
dependencies: article/video shape checks, ``nid``/title target matching and
the exact/canonical/relaxed arbitration used by the extractor.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.crawler.platforms.extract_helpers import strip_html
from src.crawler.platforms.payload_search import iter_mappings, text_at

_TIME_KEYS = (
    "publish_time",
    "publishTime",
    "publish_time_str",
    "publishTimeText",
    "create_time",
    "createTime",
    "created_at",
    "release_time",
    "ctime",
    "time",
)
_ID_KEYS = (
    "id",
    "article_id",
    "articleId",
    "video_id",
    "videoId",
    "vid",
    "nid",
    "news_id",
    "newsId",
)
_CANONICAL_ARTICLE_KEYS = (
    "article",
    "articleInfo",
    "article_info",
    "articleData",
    "article_data",
    "articleDetail",
    "article_detail",
    "detail",
    "videoInfo",
    "video_info",
    "videoData",
    "video_data",
    "curVideoMeta",
    "videoMeta",
)
_TITLE_KEYS = (
    "title",
    "articleTitle",
    "article_title",
    "newsTitle",
    "news_title",
    "videoTitle",
    "video_title",
)
_CONTENT_KEYS = (
    "content",
    "article_content",
    "articleContent",
    "content_html",
    "contentHtml",
    "body",
    "description",
    "abstract",
    "desc",
    "videoDesc",
    "video_desc",
)
_VIDEO_SHAPE_KEYS = (
    "videoTitle",
    "video_title",
    "videoDesc",
    "video_desc",
    "videoUrl",
    "video_url",
    "playUrl",
    "play_url",
    "duration",
)
_SHELL_TEXT_MARKERS = (
    "扫码下载百度app",
    "搜最新资讯、看热门视频",
    "打开百度app",
)
_SPACE = re.compile(r"\s+")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_title(value: str | None) -> str:
    return _SPACE.sub("", value or "").casefold()


def _target_text(value: str | None) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    compact = _SPACE.sub("", cleaned).casefold()
    if any(marker in compact for marker in _SHELL_TEXT_MARKERS):
        return None
    return cleaned


def _looks_like_article(node: Mapping[str, Any]) -> bool:
    return bool(text_at(node, _TITLE_KEYS)) and (
        bool(text_at(node, _CONTENT_KEYS))
        or any(key in node for key in _TIME_KEYS)
        or any(
            key in node
            for key in (
                "author",
                "authorInfo",
                "author_info",
                "mediaInfo",
                "media_info",
                "publisher",
                "source",
            )
        )
    )


def _looks_like_video(node: Mapping[str, Any]) -> bool:
    """Require target-scoped video semantics, not a generic Baidu UI card."""

    if not any(key in node for key in _VIDEO_SHAPE_KEYS):
        return False
    return bool(
        _target_text(text_at(node, _TITLE_KEYS))
        or _target_text(text_at(node, _CONTENT_KEYS))
    )


def _strip_nid_prefix(value: str) -> str:
    """百度 nid 常见业务前缀（news_/sv_）归一化为纯主体部分。"""

    text = value.strip()
    for prefix in ("news_", "sv_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _article_id_matches(node: Mapping[str, Any], wanted: str) -> bool:
    wanted_core = _strip_nid_prefix(wanted)
    for key in _ID_KEYS:
        value = node.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text == wanted or _strip_nid_prefix(text) == wanted_core:
            return True
    for key in (
        "url",
        "article_url",
        "articleUrl",
        "share_url",
        "shareUrl",
    ):
        value = node.get(key)
        if value is not None and (
            wanted in str(value) or wanted_core in str(value)
        ):
            return True
    return False


def _matches_target(
    node: Mapping[str, Any],
    wanted_id: str | None,
    wanted_title: str | None,
) -> bool:
    if wanted_id and _article_id_matches(node, wanted_id):
        return True
    if wanted_title:
        candidate = text_at(node, _TITLE_KEYS)
        return _normalize_title(candidate) == _normalize_title(wanted_title)
    return not wanted_id


def _article_signature(node: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Dedupe key for canonical-exception nodes across duplicated payloads."""

    title = _normalize_title(text_at(node, _TITLE_KEYS))
    ids = tuple(
        sorted(
            {
                str(node.get(key)).strip()
                for key in _ID_KEYS
                if node.get(key) is not None and str(node.get(key)).strip()
            }
        )
    )
    return title, ids


def _article_node(
    payload: Any,
    *,
    wanted_id: str | None = None,
    wanted_title: str | None = None,
    require_video_shape: bool = False,
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Return (node, match_kind); match_kind ∈ exact/canonical/relaxed."""

    exact: list[Mapping[str, Any]] = []
    canonical: list[Mapping[str, Any]] = []
    generic: list[Mapping[str, Any]] = []
    seen: set[int] = set()

    for mapping in iter_mappings(payload):
        candidates: list[tuple[Mapping[str, Any], bool]] = []
        for key in _CANONICAL_ARTICLE_KEYS:
            nested = mapping.get(key)
            if isinstance(nested, Mapping):
                candidates.append((nested, True))
        candidates.append((mapping, False))
        for candidate, is_canonical in candidates:
            marker = id(candidate)
            if marker in seen or not _looks_like_article(candidate):
                continue
            if require_video_shape and not _looks_like_video(candidate):
                continue
            seen.add(marker)
            if _matches_target(candidate, wanted_id, wanted_title):
                exact.append(candidate)
            elif is_canonical:
                canonical.append(candidate)
            else:
                generic.append(candidate)

    if exact:
        return exact[0], "exact"
    if wanted_id or wanted_title:
        # A single canonical SSR article may omit its own ID. Never make the
        # same exception for anonymous recommendation/feed cards.
        if not require_video_shape and len(canonical) == 1:
            return canonical[0], "canonical"
        return None, None
    relaxed = (canonical or generic or [None])[0]
    return (relaxed, "relaxed") if relaxed is not None else (None, None)


def _author_mapping(node: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in (
        "author",
        "authorInfo",
        "author_info",
        "account",
        "accountInfo",
        "account_info",
        "mediaInfo",
        "media_info",
        "publisher",
    ):
        value = node.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _content_of(node: Mapping[str, Any]) -> str | None:
    raw = text_at(node, _CONTENT_KEYS)
    if not raw:
        return None
    return strip_html(raw) if "<" in raw and ">" in raw else raw
