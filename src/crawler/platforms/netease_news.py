"""URL-scoped NetEase News article extractor.

NetEase article pages are server-rendered and stable, but the catalog's broad
``#content`` selector also captures sharing controls, comments and related
stories.  Metadata reports the platform as the author (``网易``), while the
visible article source is the auditable publisher.  This extractor confines
content to the article body and reads source/time from the article header.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    evaluate_value,
    found_any,
)
from src.crawler.platforms.registry import register
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_web_published_at

_ARTICLE_PROBE = r"""
() => {
  const text = (element) => (
    element ? (element.innerText || element.textContent || '').trim() : ''
  );
  const meta = (name) => {
    const element = document.querySelector(
      `meta[property="${name}"], meta[name="${name}"]`
    );
    return element ? (element.getAttribute('content') || '').trim() : '';
  };
  const article = document.querySelector(
    ".post_body, .post_text, #endText, article"
  );
  let content = '';
  const imageUrls = [];
  if (article) {
    const clone = article.cloneNode(true);
    clone.querySelectorAll(
      "script, style, noscript, .post_author, .post_statement, "
      + ".post_top, .post_recommend, [class*='post_recommend'], "
      + ".ep-source, .otitle, .share, [class*='share'], "
      + "#tie, .tie-area, [class*='comment']"
    ).forEach((node) => node.remove());
    const blocks = Array.from(
      clone.querySelectorAll("p, h2, h3, blockquote, li")
    )
      .map((node) => text(node))
      .filter(Boolean);
    content = blocks.length ? blocks.join('\n') : text(clone);

    for (const image of article.querySelectorAll('img')) {
      const url = (
        image.currentSrc ||
        image.getAttribute('data-original') ||
        image.getAttribute('data-src') ||
        image.getAttribute('src') ||
        ''
      ).trim();
      if (
        /^https?:\/\//i.test(url) &&
        !/(?:logo|icon|avatar|qrcode|pixel|tracking)/i.test(url)
      ) {
        imageUrls.push(url);
      }
    }
  }

  const info = document.querySelector(
    ".post_info, .post_time_source, [class*='time-source']"
  );
  const infoText = text(info);
  const sourceAnchor = info
    ? Array.from(info.querySelectorAll('a[href]')).find((anchor) => {
        const value = text(anchor);
        return value && value !== '举报' && !/jubao|report/i.test(anchor.className);
      })
    : null;
  const footerText = text(document.querySelector('.post_author, .ep-source'));
  const sourceMatch = `${infoText}\n${footerText}`.match(
    /(?:本文来源|来源)\s*[:：]\s*([^\n]+?)(?=\s*(?:责任编辑|作者|举报|$))/
  );
  const timeMatch = infoText.match(
    /(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?/
  );
  const canonical = (
    document.querySelector('link[rel="canonical"]')?.href ||
    meta('og:url') ||
    location.href
  );
  return {
    canonical,
    title: text(document.querySelector('h1.post_title, h1')) || meta('og:title'),
    content,
    author: text(sourceAnchor) || (sourceMatch ? sourceMatch[1].trim() : ''),
    authorUrl: sourceAnchor ? sourceAnchor.href : '',
    published: timeMatch
      ? timeMatch[0]
      : meta('article:published_time'),
    imageUrls: Array.from(new Set(imageUrls))
  };
}
"""

_ARTICLE_ID_RE = re.compile(r"/([A-Z0-9]{16})\.html$", re.IGNORECASE)


class NeteaseNewsExtractor:
    platform_keys = ("netease_news",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        probe = await evaluate_value(page, _ARTICLE_PROBE)
        if not isinstance(probe, Mapping):
            return None

        requested_id = netease_article_id(document.url)
        canonical_id = netease_article_id(_clean(probe.get("canonical")) or "")
        if requested_id and canonical_id and requested_id != canonical_id:
            return None

        published_raw = _clean(probe.get("published"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": _clean(probe.get("title")),
                "content_text": _clean(probe.get("content")),
                "author_name": _clean(probe.get("author")),
                # ``sourceAnchor`` is 网易's “本文来源” link and normally
                # targets the original article, not a publisher homepage.
                "published_at_raw": published_raw,
                "published_at_dt": (
                    parse_web_published_at(published_raw)
                    if published_raw
                    else None
                ),
            },
            source=ExtractionSource.PLATFORM_DOM,
        )
        data.image_urls = _image_urls(probe.get("imageUrls"))
        return (
            data
            if applied and found_any(data, "title", "content_text", "author_name")
            else None
        )


def netease_article_id(url: str) -> str | None:
    """Return the 16-character article ID from a NetEase article URL."""

    match = _ARTICLE_ID_RE.search(urlsplit(url).path)
    return match.group(1).upper() if match else None


def _image_urls(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            item.strip()
            for item in value
            if isinstance(item, str)
            and item.strip().startswith(("http://", "https://"))
        )
    )


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


register(NeteaseNewsExtractor())
