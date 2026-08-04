"""Baidu Baijiahao article and hot-detail extractor.

Public article pages can expose the requested item through a live SSR global,
embedded hydration JSON, an article XHR, or the rendered DOM.  The page also
contains recommendation cards with similar title/author shapes, so candidates
are matched to the URL article ID or hot-detail title before they are accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.baijiahao_nodes import (
    _TIME_KEYS,
    _TITLE_KEYS,
    _article_node,
    _article_signature,
    _author_mapping,
    _clean,
    _content_of,
    _target_text,
)
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    epoch_to_datetime,
    evaluate_json,
    evaluate_value,
    found_any,
)
from src.crawler.platforms.payload_search import epoch_at, text_at
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

_VIDEO_LANDING_PATHS = (
    "/newspage/data/videolanding",
    "/newspage/data/landingshare",
)


class BaijiahaoExtractor:
    platform_keys = ("baijiahao",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        wanted_id, wanted_title = _url_target(document.url)
        strict_video_target = is_video_landing_url(document.url)
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
        exact_hit: tuple[Mapping[str, Any], ExtractionSource] | None = None
        canonical_hits: list[tuple[Mapping[str, Any], ExtractionSource]] = []
        seen_canonical: set[tuple[str, tuple[str, ...]]] = set()
        relaxed_hit: tuple[Mapping[str, Any], ExtractionSource] | None = None
        for payload, source in payloads:
            node, match_kind = _article_node(
                payload,
                wanted_id=wanted_id,
                wanted_title=wanted_title,
                require_video_shape=strict_video_target,
            )
            if node is None:
                continue
            if match_kind == "exact":
                exact_hit = (node, source)
                break
            if match_kind == "canonical":
                signature = _article_signature(node)
                if signature not in seen_canonical:
                    seen_canonical.add(signature)
                    canonical_hits.append((node, source))
            elif relaxed_hit is None:
                relaxed_hit = (node, source)
        # 仲裁：exact 全局优先；canonical 例外只在跨全部载荷全局唯一时生效，
        # 避免首个载荷里的推荐文章冒充目标（证据一致性铁律）。
        chosen = exact_hit or (
            canonical_hits[0] if len(canonical_hits) == 1 else None
        ) or relaxed_hit
        if chosen is not None:
            applied += _apply_article(data, chosen[0], document.url, chosen[1])

        if not strict_video_target and (
            not data.title
            or not data.content_text
            or not data.author_name
            or not data.published_at
        ):
            applied += await _from_dom(data, page, document.url)
        if not strict_video_target:
            applied += _from_meta(data, document)
        if not strict_video_target and not data.title and wanted_title:
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
            "title": _target_text(text_at(node, _TITLE_KEYS)),
            "content_text": _target_text(_content_of(node)),
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


def _url_target(url: str) -> tuple[str | None, str | None]:
    query = parse_qs(urlsplit(url).query, keep_blank_values=False)
    article_id = _clean((query.get("id") or query.get("nid") or [None])[0])
    title = _clean((query.get("title") or [None])[0])
    return article_id, title


def is_video_landing_url(url: str | None) -> bool:
    """True for MBD video share pages whose ``nid`` must match exactly."""

    if not url:
        return False
    parsed = urlsplit(url)
    return (
        parsed.hostname == "mbd.baidu.com"
        and parsed.path.casefold() in _VIDEO_LANDING_PATHS
        and bool(parse_qs(parsed.query).get("nid"))
    )


register(BaijiahaoExtractor())
