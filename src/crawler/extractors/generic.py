"""JSON-LD, meta, generic DOM and visible-text extraction fallbacks."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from src.crawler.extractors.base import ImageCandidate, RenderedDocument
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_web_published_at


DOCUMENT_SCRIPT = """
(platformSelectors) => {
  const text = (element) => element ? (element.innerText || element.textContent || '').trim() : '';
  const pick = (selectors, field) => {
    for (const selector of selectors || []) {
      const element = document.querySelector(selector);
      if (!element) continue;
      if (field.endsWith('_url') && element.href) return element.href;
      const value = text(element) || element.getAttribute('content') || element.getAttribute('datetime') || '';
      if (value) return value.trim();
    }
    return '';
  };
  const meta = {};
  for (const element of document.querySelectorAll('meta[name], meta[property], meta[itemprop]')) {
    const key = (element.getAttribute('property') || element.getAttribute('name') || element.getAttribute('itemprop') || '').toLowerCase();
    const value = (element.getAttribute('content') || '').trim();
    if (key && value && !meta[key]) meta[key] = value;
  }
  const domSelectors = {
    content_text: ['article', '[role="main"]', 'main', '.article-content', '.post-content', '.content'],
    author_name: ['[rel="author"]', '.author-name', '.username', '[class*="author"] [class*="name"]'],
    author_url: ['a[rel="author"]', '.author-name a', '[class*="author"] a'],
    published_at: ['time[datetime]', 'time', '[class*="publish-time"]', '[class*="date"]']
  };
  const domValues = {};
  for (const [field, selectors] of Object.entries(domSelectors)) domValues[field] = pick(selectors, field);
  domValues.text_type_hint = document.querySelector(
    '[data-content-type="comment"], [data-page-type="comment"], .comment-detail-page, [class*="commentDetail"]'
  ) ? '评论回复' : '';
  const platformValues = {};
  for (const [field, selectors] of Object.entries(platformSelectors || {})) platformValues[field] = pick(selectors, field);
  const images = Array.from(document.images).map((image) => ({
    url: image.currentSrc || image.src || '',
    width: image.naturalWidth || image.width || 0,
    height: image.naturalHeight || image.height || 0,
    alt: image.alt || ''
  }));
  return {
    url: location.href,
    title: document.title || '',
    visibleText: text(document.body),
    canonicalUrl: document.querySelector('link[rel="canonical"]')?.href || '',
    meta,
    jsonLd: Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map((node) => node.textContent || ''),
    domValues,
    platformValues,
    images
  };
}
"""


class GenericExtractor:
    def __init__(self, summary_max_chars: int = 2_000) -> None:
        self._summary_max_chars = summary_max_chars

    async def collect_document(
        self,
        page: Any,
        platform_selectors: Mapping[str, tuple[str, ...]] | None = None,
    ) -> RenderedDocument:
        raw = await page.evaluate(DOCUMENT_SCRIPT, dict(platform_selectors or {}))
        return RenderedDocument(
            url=str(raw.get("url") or page.url),
            title=str(raw.get("title") or ""),
            visible_text=str(raw.get("visibleText") or ""),
            canonical_url=str(raw.get("canonicalUrl") or ""),
            meta={str(key).lower(): str(value) for key, value in (raw.get("meta") or {}).items()},
            json_ld=tuple(str(value) for value in raw.get("jsonLd") or () if value),
            dom_values={str(key): str(value) for key, value in (raw.get("domValues") or {}).items()},
            platform_values={str(key): str(value) for key, value in (raw.get("platformValues") or {}).items()},
            images=tuple(
                ImageCandidate(
                    url=str(item.get("url") or ""),
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                    alt=str(item.get("alt") or ""),
                )
                for item in raw.get("images") or ()
            ),
        )

    def extract(self, document: RenderedDocument) -> PageData:
        data = PageData(final_url=document.url)
        self._extract_json_ld(document, data)
        self._extract_meta(document.meta, data)
        self._extract_dom(document, data)
        data.image_urls.extend(self._image_urls(document.images))
        self.finalize(data)
        return data

    def _extract_json_ld(self, document: RenderedDocument, data: PageData) -> None:
        for raw in document.json_ld:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for node in _json_nodes(payload):
                if not isinstance(node, dict):
                    continue
                self._set(data, "title", node.get("headline") or node.get("name"), ExtractionSource.JSON_LD)
                self._set(
                    data,
                    "content_text",
                    node.get("articleBody") or node.get("description") or node.get("caption"),
                    ExtractionSource.JSON_LD,
                )
                self._set(data, "published_at_raw", node.get("datePublished") or node.get("uploadDate"), ExtractionSource.JSON_LD)
                self._set(data, "store_name", _name(node.get("seller") or node.get("brand")), ExtractionSource.JSON_LD)
                author = node.get("author") or node.get("creator")
                self._set(data, "author_name", _name(author), ExtractionSource.JSON_LD)
                self._set(data, "author_id", _identifier(author), ExtractionSource.JSON_LD)
                self._set(data, "author_url", _url(author), ExtractionSource.JSON_LD)
                data.image_urls.extend(_json_images(node.get("image") or node.get("thumbnailUrl")))

    def _extract_meta(self, meta: Mapping[str, str], data: PageData) -> None:
        self._set(data, "title", _first(meta, "og:title", "twitter:title", "title"), ExtractionSource.META)
        self._set(
            data,
            "content_text",
            _first(meta, "article:body", "og:description", "twitter:description", "description"),
            ExtractionSource.META,
        )
        self._set(data, "author_name", _first(meta, "author", "article:author", "byl"), ExtractionSource.META)
        self._set(
            data,
            "author_id",
            _first(meta, "profile:username", "account:id", "author:id"),
            ExtractionSource.META,
        )
        self._set(
            data,
            "published_at_raw",
            _first(meta, "article:published_time", "datepublished", "publishdate", "date"),
            ExtractionSource.META,
        )
        for key in ("og:image", "twitter:image", "image"):
            if meta.get(key):
                data.image_urls.append(meta[key])

    def _extract_dom(self, document: RenderedDocument, data: PageData) -> None:
        self._set(data, "title", document.title, ExtractionSource.GENERIC_DOM)
        self._set(data, "content_text", document.dom_values.get("content_text"), ExtractionSource.GENERIC_DOM)
        self._set(data, "author_name", document.dom_values.get("author_name"), ExtractionSource.GENERIC_DOM)
        self._set(data, "author_url", document.dom_values.get("author_url"), ExtractionSource.GENERIC_DOM)
        self._set(data, "published_at_raw", document.dom_values.get("published_at"), ExtractionSource.GENERIC_DOM)
        if document.dom_values.get("text_type_hint") == "评论回复":
            data.text_type_hint = "评论回复"
        if not data.content_text:
            self._set(data, "content_text", document.visible_text, ExtractionSource.VISIBLE_TEXT)

    def finalize(self, data: PageData) -> None:
        data.title = clean_text(data.title)
        data.content_text = clean_text(data.content_text)
        data.author_name = clean_text(data.author_name)
        data.store_name = clean_text(data.store_name)
        if data.published_at_raw:
            data.published_at = parse_web_published_at(data.published_at_raw)
        summary = data.content_text or ""
        data.summary_truncated = len(summary) > self._summary_max_chars
        data.content_summary = summary[: self._summary_max_chars]
        data.image_urls = list(dict.fromkeys(url for url in data.image_urls if _valid_image_url(url)))

    @staticmethod
    def _set(data: PageData, field: str, value: Any, source: ExtractionSource) -> None:
        if getattr(data, field) or value is None:
            return
        normalized = str(value).strip()
        if normalized:
            setattr(data, field, normalized)
            data.field_sources[field] = source

    @staticmethod
    def _image_urls(images: tuple[ImageCandidate, ...]) -> list[str]:
        return [
            image.url
            for image in images
            if (image.width == 0 or image.width >= 80)
            and (image.height == 0 or image.height >= 80)
            and not re.search(r"(?:avatar|icon|logo|sprite|tracking|pixel)", f"{image.url} {image.alt}", re.I)
        ]


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(value).splitlines()]
    unique = list(dict.fromkeys(line for line in lines if line))
    return "\n".join(unique) or None


def _json_nodes(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [node for item in value for node in _json_nodes(item)]
    if isinstance(value, dict) and isinstance(value.get("@graph"), list):
        return [value, *value["@graph"]]
    return [value]


def _name(value: Any) -> str | None:
    if isinstance(value, list):
        return _name(value[0]) if value else None
    if isinstance(value, dict):
        return value.get("name")
    return str(value) if value else None


def _identifier(value: Any) -> str | None:
    if isinstance(value, list):
        return _identifier(value[0]) if value else None
    if not isinstance(value, dict):
        return None
    identifier = value.get("identifier")
    if isinstance(identifier, dict):
        return identifier.get("value") or identifier.get("name")
    return str(identifier) if identifier else None


def _url(value: Any) -> str | None:
    if isinstance(value, list):
        return _url(value[0]) if value else None
    return value.get("url") if isinstance(value, dict) else None


def _json_images(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [url for item in value for url in _json_images(item)]
    if isinstance(value, dict):
        return _json_images(value.get("url") or value.get("contentUrl"))
    return []


def _first(values: Mapping[str, str], *keys: str) -> str | None:
    return next((values[key] for key in keys if values.get(key)), None)


def _valid_image_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
