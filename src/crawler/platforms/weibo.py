"""Weibo dedicated extractor.

After the operator-managed login, ``weibo.com`` detail pages fetch the mblog
JSON (``/ajax/statuses/show``) which the network collector captures.  The
``text_raw``/``user``/``created_at`` node gives every template field.  A DOM
probe covers pages where the XHR was not captured.
"""
from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Any

from src.crawler.api_assist import weibo_bid, weibo_mblog
from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    evaluate_value,
    found_any,
    strip_html,
)
from src.crawler.platforms.payload_search import iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import (
    DEFAULT_TIMEZONE,
    parse_published_at_from_text,
    pick_latest_published_at,
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_DOM_PROBE = """
() => {
  const pick = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element && element.textContent.trim()) return element.textContent.trim();
    }
    return '';
  };
  const inCommentRegion = (element) => {
    let node = element;
    while (node) {
      const label = String(node.className || '') + ' ' + String(node.id || '');
      if (/comment|reply|recommend|related/i.test(label)) return true;
      node = node.parentElement;
    }
    return false;
  };
  const authorLink = document.querySelector(
    "[class*='head_name'] a, [class*='ALink_default'][href*='/u/'], a[href*='/u/'][class*='name']"
  );
  // 详情页存在多个时间（主帖/被转发原帖/评论/推荐），全部收集交给 Python 侧
  // 取距离现在最近的主帖区候选。
  const times = Array.from(document.querySelectorAll(
    "[class*='head-info'] a[href*='weibo.com'], [class*='from'] a, time"
  )).slice(0, 10).map((element) => ({
    text: (element.textContent || '').trim(),
    inComment: inCommentRegion(element)
  })).filter((item) => item.text);
  return {
    content: pick(["[class*='detail_wbtext']", "[node-type='feed_list_content']"]),
    author: pick(["[class*='head_name']", "[class*='head-info'] [class*='name']"]),
    authorUrl: authorLink ? authorLink.href : '',
    times
  };
}
"""


class WeiboExtractor:
    platform_keys = ("weibo",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        data = PageData(final_url=document.url)
        applied = self._from_mblog_json(data, document)
        if not found_any(data, "content_text", "author_name"):
            applied += await self._from_dom(data, page)
        if not found_any(data, "content_text", "author_name"):
            applied += await self._from_api(data, page, document)
        return data if applied and found_any(data, "content_text", "author_name") else None

    def _from_mblog_json(self, data: PageData, document: RenderedDocument) -> int:
        for payload in document.network_payloads:
            mblog = _mblog_node(payload)
            if mblog is None:
                continue
            return _apply_mblog(data, mblog, ExtractionSource.NETWORK_JSON)
        return 0

    async def _from_api(
        self,
        data: PageData,
        page: Any,
        document: RenderedDocument,
    ) -> int:
        """Additive fallback: public ``m.weibo.cn`` mobile JSON endpoint.

        The desktop detail page demands login, but the mobile statuses/show
        JSON often answers a visitor session riding the browser cookies.
        """

        bid = weibo_bid(document.url)
        if not bid:
            return 0
        mblog = await weibo_mblog(page, bid)
        if mblog is None:
            return 0
        return _apply_mblog(data, mblog, ExtractionSource.NETWORK_JSON)

    async def _from_dom(self, data: PageData, page: Any) -> int:
        probe = await evaluate_value(page, _DOM_PROBE)
        if not isinstance(probe, Mapping):
            return 0
        parsed_candidates: list[tuple[datetime, bool]] = []
        for text, in_comment in _dom_time_candidates(probe):
            parsed = _parse_weibo_time(text)
            if parsed is not None:
                parsed_candidates.append((parsed, in_comment))
        published = pick_latest_published_at(parsed_candidates)
        return apply_json_fields(
            data,
            {
                "content_text": probe.get("content") or None,
                "author_name": probe.get("author") or None,
                "author_url": probe.get("authorUrl") or None,
                "published_at_dt": published,
            },
        )


def _dom_time_candidates(probe: Mapping[str, Any]) -> list[tuple[str, bool]]:
    """Collect ``(text, in_comment)`` time candidates from the DOM probe.

    兼容旧探针的单值 ``time`` 键（按主帖区候选处理）。
    """

    candidates: list[tuple[str, bool]] = []
    raw = probe.get("times")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                text = str(item.get("text") or "").strip()
                if text:
                    candidates.append((text, bool(item.get("inComment"))))
    legacy = str(probe.get("time") or "").strip()
    if legacy:
        candidates.append((legacy, False))
    return candidates


def _apply_mblog(
    data: PageData,
    mblog: Mapping[str, Any],
    source: ExtractionSource,
) -> int:
    user = mblog.get("user")
    user = user if isinstance(user, Mapping) else {}
    user_id = text_at(user, ("idstr", "idStr", "id"))
    published = _parse_weibo_time(text_at(mblog, ("created_at",)))
    return apply_json_fields(
        data,
        {
            "content_text": text_at(mblog, ("text_raw",)) or strip_html(
                text_at(mblog, ("text",)) or ""
            ),
            "author_name": text_at(user, ("screen_name", "name")),
            "author_id": user_id,
            "author_url": f"https://weibo.com/u/{user_id}" if user_id else None,
            "published_at_dt": published,
        },
        source=source,
    )


def _mblog_node(payload: Any) -> Mapping[str, Any] | None:
    for mapping in iter_mappings(payload):
        if "user" in mapping and ("text_raw" in mapping or "text" in mapping):
            user = mapping.get("user")
            if isinstance(user, Mapping) and (
                "screen_name" in user or "name" in user
            ):
                return mapping
    return None


def _parse_weibo_time(value: str | None) -> datetime | None:
    """Parse ``Wed Jul 01 12:00:00 +0800 2026`` without locale dependence.

    中文格式（「9月21日 10:00」「今天 10:00」「1小时前」）与带标签的长文本
    回退到通用网页时间解析。
    """

    if not value:
        return None
    parts = value.split()
    if len(parts) >= 6 and parts[1] in _MONTHS:
        try:
            month = _MONTHS[parts[1]]
            day = int(parts[2])
            hour, minute, second = (int(piece) for piece in parts[3].split(":"))
            year = int(parts[5])
            return datetime(year, month, day, hour, minute, second, tzinfo=DEFAULT_TIMEZONE)
        except (ValueError, IndexError):
            return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=DEFAULT_TIMEZONE)
        except ValueError:
            continue
    return parse_published_at_from_text(value)


register(WeiboExtractor())
