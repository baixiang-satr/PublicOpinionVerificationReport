"""Baidu Tieba dedicated extractor.

Tieba thread pages are classic HTML: the first floor (楼主) holds the audited
content.  A DOM probe reads the thread title, first-floor body and author,
plus the ``.tail-info`` floor metadata for the publish time.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
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
  const firstFloor = document.querySelector('.l_post, .p_postlist .l_post, [class*="l_post"]');
  const pickIn = (root, selectors) => {
    if (!root) return '';
    for (const selector of selectors) {
      const element = root.querySelector(selector);
      const value = text(element);
      if (value) return value;
    }
    return '';
  };
  const title = text(document.querySelector('.core_title_txt, .core_title, h1'));
  const content = pickIn(firstFloor, ['.d_post_content', '[class*="d_post_content"]'])
    || text(document.querySelector('.d_post_content'));
  const author = pickIn(firstFloor, ['.p_author_name', '.d_name a', '.d_name', '[class*="author"]']);
  const authorAnchor = firstFloor
    ? firstFloor.querySelector('a.p_author_name, .d_name a, a[href*="/home/main"]')
    : null;
  const tailInfos = Array.from(
    (firstFloor || document).querySelectorAll('.tail-info')
  ).map((node) => text(node)).filter(Boolean);
  return {
    title,
    content,
    author,
    authorUrl: authorAnchor ? authorAnchor.href : '',
    tailInfos
  };
}
"""

_DATE_HINT = re.compile(r"(?:19|20)\d{2}[-/.年]\d{1,2}")


class TiebaExtractor:
    platform_keys = ("tieba",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        probe = await evaluate_value(page, _DOM_PROBE)
        if not isinstance(probe, Mapping):
            return None
        published_raw = _pick_time(probe.get("tailInfos"))
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": _clean(probe.get("title")),
                "content_text": _clean(probe.get("content")),
                "author_name": _clean(probe.get("author")),
                "author_url": _clean(probe.get("authorUrl")),
                "published_at_raw": published_raw,
                "published_at_dt": parse_web_published_at(published_raw) if published_raw else None,
            },
        )
        return data if applied and found_any(data, "content_text", "title") else None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _pick_time(tail_infos: Any) -> str | None:
    if not isinstance(tail_infos, (list, tuple)):
        return None
    for item in tail_infos:
        if isinstance(item, str) and _DATE_HINT.search(item):
            return item.strip()
    return None


register(TiebaExtractor())
