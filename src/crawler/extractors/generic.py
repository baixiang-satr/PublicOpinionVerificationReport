"""JSON-LD, meta, generic DOM and visible-text extraction fallbacks."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from src.crawler.extractors.base import ImageCandidate, RenderedDocument
from src.crawler.content_classifier import initialize_content_kind
from src.crawler.field_resolver import consider_field
from src.crawler.structured_data import StructuredDataExtractor
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_web_published_at


DOCUMENT_SCRIPT = r"""
(platformSelectors) => {
  const text = (element) => element ? (element.innerText || element.textContent || '').trim() : '';
  const isSelfProfile = (href) => /\/user\/self(?:[/?#]|$)/i.test(href || '');
  const pick = (selectors, field) => {
    for (const selector of selectors || []) {
      const elements = Array.from(document.querySelectorAll(selector)).slice(0, 20);
      for (const element of elements) {
        if (field.endsWith('_url')) {
          const link = element.matches?.('a[href]')
            ? element
            : (element.closest?.('a[href]') || element.querySelector?.('a[href]'));
          // /user/self 是查看者自己的主页（抖音登录后导航栏），绝非作者
          if (link?.href && !isSelfProfile(link.href)) return link.href;
          continue;
        }
        const value = text(element) || element.getAttribute('content') || element.getAttribute('datetime') || '';
        if (value) return value.trim();
      }
    }
    return '';
  };
  const meta = {};
  for (const element of document.querySelectorAll('meta[name], meta[property], meta[itemprop]')) {
    const key = (element.getAttribute('property') || element.getAttribute('name') || element.getAttribute('itemprop') || '').toLowerCase();
    const value = (element.getAttribute('content') || '').trim();
    if (key && value && !meta[key]) meta[key] = value;
  }
  const embeddedPayloads = [];
  const pushPayload = (value) => {
    if (value === null || value === undefined) return;
    try {
      const serialized = typeof value === 'string' ? value : JSON.stringify(value);
      if (!serialized || serialized.length > 2000000) return;
      const parsed = typeof value === 'string' ? JSON.parse(serialized) : JSON.parse(serialized);
      if (parsed && (Array.isArray(parsed) || typeof parsed === 'object')) {
        embeddedPayloads.push(parsed);
      }
    } catch (_) {}
  };
  const structuredScriptSelector = [
    'script[type="application/json"]',
    'script#__NEXT_DATA__',
    'script#__NUXT_DATA__',
    'script#RENDER_DATA',
    'script[data-hypernova-key]',
  ].join(',');
  for (const node of Array.from(document.querySelectorAll(structuredScriptSelector)).slice(0, 30)) {
    pushPayload(node.textContent || '');
  }
  for (const key of [
    '__NEXT_DATA__',
    '__NUXT__',
    '__INITIAL_STATE__',
    '__APOLLO_STATE__',
    '_SSR_HYDRATED_DATA',
    '__INITIAL_PROPS__',
  ]) {
    try { pushPayload(window[key]); } catch (_) {}
  }
  const bodyText = text(document.body);
  if (/^\s*[\[{]/.test(bodyText)) pushPayload(bodyText);
  const domSelectors = {
    content_text: ['article', '[role="main"]', 'main', '.article-content', '.post-content', '.content'],
    author_name: [
      '[rel="author"]', '.author-name', '.username',
      '[class*="author"] [class*="name"]', '[class*="user"] [class*="name"]',
      '[class*="creator"] [class*="name"]', '[class*="owner"] [class*="name"]',
      '[class*="nickname"]', '[data-e2e*="author"]', '[data-e2e*="user-name"]'
    ],
    author_url: [
      'a[rel="author"]', '.author-name a', '[class*="author"] a',
      '.avatar a', '.user-info a', '.profile-link',
      '[class*="profile"] a', 'a[href*="/user/"]',
      'a[href*="/profile/"]', 'a[href*="/author/"]',
      'a[href*="/u/"]', 'a[href*="/space/"]',
      '[class*="up-info"] a', '[class*="author-face"] a'
    ],
    published_at: [
      'time[datetime]', 'time', '[class*="publish-time"]',
      '[class*="date"]', '[class*="time"]', '[class*="create-time"]',
      '[datetime]', '[class*="post-time"]',
      '[class*="sub-time"]', '[class*="timestamp"]'
    ]
  };
  const domValues = {};
  for (const [field, selectors] of Object.entries(domSelectors)) domValues[field] = pick(selectors, field);
  if (!domValues.author_url) {
    const currentUrl = new URL(location.href);
    const candidates = Array.from(document.querySelectorAll('a[href]')).map((anchor) => {
      let target;
      try { target = new URL(anchor.href, location.href); } catch (_) { return null; }
      if (!['http:', 'https:'].includes(target.protocol)) return null;
      if (target.href.split('#')[0] === currentUrl.href.split('#')[0]) return null;
      if (/\/user\/self(?:\/|$)/i.test(target.pathname)) return null;
      if (/\/(?:login|signin|register|share|search|topic|tag|comment)(?:\/|$)/i.test(target.pathname)) return null;
      const context = [
        anchor.getAttribute('rel') || '',
        anchor.getAttribute('class') || '',
        anchor.parentElement?.getAttribute('class') || '',
        anchor.parentElement?.parentElement?.getAttribute('class') || '',
      ].join(' ');
      let score = 0;
      if (/\bauthor\b/i.test(anchor.getAttribute('rel') || '')) score += 120;
      if (/(?:author|creator|profile|user|owner|uploader|nickname|avatar|up-info)/i.test(context)) score += 60;
      if (/\/(?:user|profile|author|u|space|people|account|member|creator|up)(?:\/|$)/i.test(target.pathname)) score += 45;
      if (anchor.querySelector('img[alt], [class*="avatar"]')) score += 20;
      const label = text(anchor) || anchor.getAttribute('aria-label') || anchor.querySelector('img')?.alt || '';
      if (label && label.length <= 80) score += 10;
      if (target.pathname === '/' || target.pathname === '') score -= 80;
      return { href: target.href, label: label.trim(), score };
    }).filter(Boolean).sort((left, right) => right.score - left.score);
    if (candidates[0]?.score >= 55) {
      domValues.author_url = candidates[0].href;
      if (!domValues.author_name && candidates[0].label) domValues.author_name = candidates[0].label;
    }
  }
  domValues.text_type_hint = document.querySelector(
    '[data-content-type="comment"], [data-page-type="comment"], .comment-detail-page, [class*="commentDetail"]'
  ) ? '评论回复' : '';
  const platformValues = {};
  for (const [field, selectors] of Object.entries(platformSelectors || {})) platformValues[field] = pick(selectors, field);
  const images = Array.from(document.images).map((image) => ({
    url: image.currentSrc || image.src || '',
    width: image.naturalWidth || image.width || 0,
    height: image.naturalHeight || image.height || 0,
    alt: image.alt || '',
    context: [
      image.className || '',
      image.id || '',
      image.parentElement?.className || ''
    ].join(' '),
    inContent: Boolean(image.closest(
      'article, main, [role="main"], [class*="article"], '
      + '[class*="content"], [class*="detail"], [class*="post"]'
    ))
  }));
  return {
    url: location.href,
    title: document.title || '',
    visibleText: bodyText,
    canonicalUrl: document.querySelector('link[rel="canonical"]')?.href || '',
    meta,
    jsonLd: Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map((node) => node.textContent || ''),
    embeddedPayloads,
    domValues,
    platformValues,
    images
  };
}
"""


class GenericExtractor:
    def __init__(self, summary_max_chars: int = 2_000) -> None:
        self._summary_max_chars = summary_max_chars
        self._structured = StructuredDataExtractor()

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
            embedded_payloads=tuple(raw.get("embeddedPayloads") or ()),
            dom_values={str(key): str(value) for key, value in (raw.get("domValues") or {}).items()},
            platform_values={str(key): str(value) for key, value in (raw.get("platformValues") or {}).items()},
            images=tuple(
                ImageCandidate(
                    url=str(item.get("url") or ""),
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                    alt=str(item.get("alt") or ""),
                    context=str(item.get("context") or ""),
                    in_content=bool(item.get("inContent")),
                )
                for item in raw.get("images") or ()
            ),
        )

    def extract(self, document: RenderedDocument) -> PageData:
        data = PageData(final_url=document.url)
        self._extract_json_ld(document, data)
        self._structured.apply(
            document.embedded_payloads,
            data,
            ExtractionSource.EMBEDDED_JSON,
        )
        self._structured.apply(
            document.network_payloads,
            data,
            ExtractionSource.NETWORK_JSON,
        )
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
                self._set(
                    data,
                    "published_at_raw",
                    node.get("datePublished")
                    or node.get("uploadDate")
                    or node.get("dateCreated")
                    or node.get("dateModified"),
                    ExtractionSource.JSON_LD,
                )
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
        meta_author = _first(meta, "author", "article:author", "byl")
        if meta_author and str(meta_author).strip().lower().startswith(("http://", "https://")):
            self._set(data, "author_url", meta_author, ExtractionSource.META)
        else:
            self._set(data, "author_name", meta_author, ExtractionSource.META)
        self._set(
            data,
            "author_id",
            _first(meta, "profile:username", "account:id", "author:id"),
            ExtractionSource.META,
        )
        self._set(
            data,
            "published_at_raw",
            _first(
                meta,
                "article:published_time", "datepublished", "publishdate", "date",
                "datecreated", "date_created", "pubdate", "sailthru.date",
                "weibo:date", "weibo:time", "weibo:timestamp",
                "bytedance:published_time", "bytedance:date",
                "bili:date", "bili:pubdate",
            ),
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
        data.author_name = clean_author_name(data.author_name)
        data.store_name = clean_text(data.store_name)
        if data.published_at_raw:
            data.published_at = parse_web_published_at(data.published_at_raw)
            source = data.field_sources.get("published_at_raw")
            if source is not None:
                data.field_sources["published_at"] = source
                data.field_confidences["published_at"] = (
                    data.field_confidences.get("published_at_raw", 0.0)
                )
        initialize_content_kind(
            data,
            summary_max_chars=self._summary_max_chars,
        )
        data.image_urls = list(dict.fromkeys(url for url in data.image_urls if _valid_image_url(url)))

    @staticmethod
    def _set(data: PageData, field: str, value: Any, source: ExtractionSource) -> None:
        consider_field(data, field, value, source)

    @staticmethod
    def _image_urls(images: tuple[ImageCandidate, ...]) -> list[str]:
        content_images = tuple(image for image in images if image.in_content)
        candidates = content_images or images
        return [
            image.url
            for image in candidates
            if (image.width == 0 or image.width >= 80)
            and (image.height == 0 or image.height >= 80)
            and (
                image.width == 0
                or image.height == 0
                or image.width * image.height >= 10_000
            )
            and not re.search(
                r"(?:avatar|icon|logo|sprite|tracking|pixel|emoji|qrcode|"
                r"banner|advert|placeholder)",
                f"{image.url} {image.alt} {image.context}",
                re.I,
            )
        ]


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(value).splitlines()]
    unique = list(dict.fromkeys(line for line in lines if line))
    return "\n".join(unique) or None


def clean_author_name(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    if "\n" in text:
        text = text.splitlines()[0].strip()
    if not text or len(text) > 100:
        return None
    compact = text.casefold().replace(" ", "")
    if compact in {
        "首页",
        "登录",
        "点击登录",
        "未登录",
        "作者",
        "用户",
        "account",
        "profile",
    }:
        return None
    if re.search(r"(?:19|20)\d{2}[-/.年]\d{1,2}.*(?:来源|source)", text, re.I):
        return None
    return text


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
    if isinstance(value, str) and value.startswith(("http://", "https://", "/")):
        return value
    if isinstance(value, dict):
        candidate = value.get("url") or value.get("@id") or value.get("sameAs")
        return _url(candidate)
    return None


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
