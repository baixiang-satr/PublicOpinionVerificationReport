"""Baidu Baijiahao article and hot-detail extractor.

Public article pages can expose the requested item through a live SSR global,
embedded hydration JSON, an article XHR, or the rendered DOM.  The page also
contains recommendation cards with similar title/author shapes, so candidates
are matched to the URL article ID or hot-detail title before they are accepted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    epoch_to_datetime,
    evaluate_json,
    evaluate_value,
    found_any,
    strip_html,
)
from src.crawler.platforms.payload_search import epoch_at, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_web_published_at

_STATE_SCRIPT = """
() => {
  try {
    const state =
      window.__PRELOADED_STATE__
      || window.__INITIAL_STATE__
      || window.__SSR_DATA__
      || window._SSR_HYDRATED_DATA
      || window.__NEXT_DATA__;
    return state ? JSON.stringify(state) : null;
  } catch (error) {
    return null;
  }
}
"""

_DOM_PROBE = """
() => {
  const pick = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const value = element
        ? (element.innerText || element.textContent || '').trim()
        : '';
      if (value) return value;
    }
    return '';
  };
  const authorLink = document.querySelector(
    ".author-name a, [class*='authorName'] a, "
    + "[class*='author-info'] a, a[href*='author.baidu.com']"
  );
  const publishedNode = document.querySelector(
    "time, .date, [class*='publishTime'], [class*='publish-time']"
  );
  return {
    title: pick([
      'h1',
      '.article-title',
      "[class*='articleTitle']",
      "[class*='article-title']",
      "[class*='detail-title']"
    ]),
    content: pick([
      'article',
      '.article-content',
      "[class*='articleContent']",
      "[class*='article-content']",
      "[class*='articleWrap']",
      'main'
    ]),
    author: pick([
      '.author-name',
      "[class*='authorName']",
      "[class*='author-name']",
      "[class*='accountName']",
      "[class*='source']"
    ]),
    authorUrl: authorLink ? authorLink.href : '',
    authorId: authorLink
      ? (
          authorLink.getAttribute('data-uk')
          || authorLink.getAttribute('data-id')
          || ''
        )
      : '',
    published: publishedNode
      ? (
          publishedNode.getAttribute('datetime')
          || publishedNode.textContent
          || ''
        ).trim()
      : ''
  };
}
"""

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
_SPACE = re.compile(r"\s+")


class BaijiahaoExtractor:
    platform_keys = ("baijiahao",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        wanted_id, wanted_title = _url_target(document.url)
        data = PageData(final_url=document.url)
        applied = 0

        state = await evaluate_json(page, _STATE_SCRIPT)
        payloads: list[tuple[Any, ExtractionSource]] = []
        if state is not None:
            payloads.append((state, ExtractionSource.EMBEDDED_JSON))
        payloads.extend(
            (payload, ExtractionSource.EMBEDDED_JSON)
            for payload in document.embedded_payloads
        )
        payloads.extend(
            (payload, ExtractionSource.NETWORK_JSON)
            for payload in document.network_payloads
        )
        for payload, source in payloads:
            node = _article_node(
                payload,
                wanted_id=wanted_id,
                wanted_title=wanted_title,
            )
            if node is None:
                continue
            applied += _apply_article(data, node, document.url, source)
            if found_any(data, "content_text", "title"):
                break

        if (
            not data.title
            or not data.content_text
            or not data.author_name
            or not data.published_at
        ):
            applied += await _from_dom(data, page, document.url)
        applied += _from_meta(data, document)
        if not data.title and wanted_title:
            applied += apply_json_fields(
                data,
                {"title": wanted_title},
                source=ExtractionSource.DERIVED_URL,
            )
        return data if applied and found_any(data, "content_text", "title") else None


def _apply_article(
    data: PageData,
    node: Mapping[str, Any],
    base_url: str,
    source: ExtractionSource,
) -> int:
    author = _author_mapping(node)
    published_raw = text_at(node, _TIME_KEYS)
    author_url = text_at(
        author,
        ("home_url", "homeUrl", "url", "profile_url", "profileUrl"),
    )
    if author_url:
        author_url = urljoin(base_url, author_url)
    return apply_json_fields(
        data,
        {
            "title": text_at(node, _TITLE_KEYS),
            "content_text": _content_of(node),
            "author_name": text_at(
                author,
                (
                    "name",
                    "nickname",
                    "authorName",
                    "accountName",
                    "mediaName",
                    "media_name",
                ),
            )
            or text_at(
                node,
                (
                    "source",
                    "author_name",
                    "authorName",
                    "accountName",
                    "mediaName",
                    "media_name",
                ),
            ),
            "author_id": text_at(
                author,
                ("uk", "id", "author_id", "authorId", "app_id", "appId"),
            )
            or text_at(node, ("author_id", "authorId", "uk")),
            "author_url": author_url,
            "published_at_raw": published_raw,
            "published_at_dt": epoch_to_datetime(epoch_at(node, _TIME_KEYS))
            or (
                parse_web_published_at(published_raw)
                if published_raw
                else None
            ),
        },
        source=source,
    )


async def _from_dom(data: PageData, page: Any, base_url: str) -> int:
    probe = await evaluate_value(page, _DOM_PROBE)
    if not isinstance(probe, Mapping):
        return 0
    published_raw = _clean(probe.get("published"))
    author_url = _clean(probe.get("authorUrl"))
    return apply_json_fields(
        data,
        {
            "title": _clean(probe.get("title")),
            "content_text": _clean(probe.get("content")),
            "author_name": _clean(probe.get("author")),
            "author_id": _clean(probe.get("authorId")),
            "author_url": urljoin(base_url, author_url) if author_url else None,
            "published_at_raw": published_raw,
            "published_at_dt": (
                parse_web_published_at(published_raw)
                if published_raw
                else None
            ),
        },
        source=ExtractionSource.PLATFORM_DOM,
    )


def _from_meta(data: PageData, document: RenderedDocument) -> int:
    meta = {str(key).casefold(): value for key, value in document.meta.items()}
    published_raw = (
        meta.get("article:published_time")
        or meta.get("date")
        or meta.get("pubdate")
    )
    return apply_json_fields(
        data,
        {
            "title": meta.get("og:title"),
            "content_text": meta.get("description") or meta.get("og:description"),
            "author_name": meta.get("author"),
            "published_at_raw": published_raw,
            "published_at_dt": (
                parse_web_published_at(published_raw)
                if published_raw
                else None
            ),
        },
        source=ExtractionSource.META,
    )


def _article_node(
    payload: Any,
    *,
    wanted_id: str | None = None,
    wanted_title: str | None = None,
) -> Mapping[str, Any] | None:
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
            seen.add(marker)
            if _matches_target(candidate, wanted_id, wanted_title):
                exact.append(candidate)
            elif is_canonical:
                canonical.append(candidate)
            else:
                generic.append(candidate)

    if exact:
        return exact[0]
    if wanted_id or wanted_title:
        # A single canonical SSR article may omit its own ID. Never make the
        # same exception for anonymous recommendation/feed cards.
        return canonical[0] if len(canonical) == 1 else None
    return (canonical or generic or [None])[0]


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


def _article_id_matches(node: Mapping[str, Any], wanted: str) -> bool:
    for key in _ID_KEYS:
        value = node.get(key)
        if value is not None and str(value).strip() == wanted:
            return True
    for key in (
        "url",
        "article_url",
        "articleUrl",
        "share_url",
        "shareUrl",
    ):
        value = node.get(key)
        if value is not None and wanted in str(value):
            return True
    return False


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


def _url_target(url: str) -> tuple[str | None, str | None]:
    query = parse_qs(urlsplit(url).query, keep_blank_values=False)
    article_id = _clean((query.get("id") or query.get("nid") or [None])[0])
    title = _clean((query.get("title") or [None])[0])
    return article_id, title


def _normalize_title(value: str | None) -> str:
    return _SPACE.sub("", value or "").casefold()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


register(BaijiahaoExtractor())
