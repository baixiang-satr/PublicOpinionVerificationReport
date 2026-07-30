"""Sohu Video dedicated extractor.

The catalog extractor already gets the title on ``tv.sohu.com/v/`` pages but
systematically misses the uploader.  This extractor probes the player page
DOM for the uploader block and publish time, falling back to the video
description for 信息内容.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    evaluate_value,
    found_any,
)
from src.crawler.platforms.registry import register
from src.domain.models import PageData
from src.utils.time_utils import parse_web_published_at

_DOM_PROBE = r"""
() => {
  const text = (element) => (element ? (element.textContent || '').trim() : '');
  const pick = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const value = text(element);
      if (value) return value;
    }
    return '';
  };
  const authorAnchor = document.querySelector(
    "[class*='up-info'] a, [class*='userInfo'] a, [class*='user-name'] a, [class*='up-info'] a[class*='name'], a[href*='tv.sohu.com/user'], a[href*='/u/'], a[href*='i.sohu.com']"
  );
  const meta = (name) => {
    const element = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
    return element ? (element.getAttribute('content') || '').trim() : '';
  };
  return {
    title: pick(['h1', '.video-title', '[class*="video-title"]']) || meta('og:title'),
    desc: pick(['[class*="video-desc"]', '[class*="desc"]', '.video-info']) || meta('og:description'),
    author: pick([
      '[class*="user-name"]',
      '[class*="up-name"]',
      '[class*="upName"]',
      '[class*="up_name"]',
      '[class*="userInfo"] [class*="name"]',
      '[class*="up-info"] [class*="name"]',
      '[class*="anchor"] [class*="name"]',
      '.user-name'
    ]) || meta('author') || meta('og:video:author'),
    authorUrl: authorAnchor ? authorAnchor.href : '',
    time: pick(['[class*="time"]', '[class*="date"]', 'time'])
  };
}
"""


class SohuVideoExtractor:
    platform_keys = ("sohu_video",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        probe = await evaluate_value(page, _DOM_PROBE)
        if not isinstance(probe, Mapping):
            return None
        published_raw = _clean(probe.get("time"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": _clean(probe.get("title")),
                "content_text": _clean(probe.get("desc")),
                "author_name": _clean(probe.get("author")),
                "author_url": _clean(probe.get("authorUrl")),
                "published_at_raw": published_raw,
                "published_at_dt": (
                    parse_web_published_at(published_raw) if published_raw else None
                ),
            },
        )
        return data if applied and found_any(data, "author_name", "title") else None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


register(SohuVideoExtractor())
