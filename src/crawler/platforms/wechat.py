"""Dedicated extraction for WeChat Official Accounts and Channels shares.

Both surfaces expose useful facts through a mix of page globals, narrow DOM
containers, OpenGraph metadata, and bounded hydration/network payloads.  The
extractor deliberately reads only the already-open page and never calls a
private API or attempts to bypass a login/challenge page.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.extract_helpers import (
    apply_json_fields,
    epoch_to_datetime,
    evaluate_json,
    evaluate_value,
    found_any,
)
from src.crawler.platforms.payload_search import epoch_at, iter_mappings, text_at
from src.crawler.platforms.registry import register
from src.domain.models import ExtractionSource, PageData

_WECHAT_PAGE_PROBE = r"""
() => {
  const text = (element) => element
    ? (element.innerText || element.textContent || '').trim()
    : '';
  const pick = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const value = text(element)
        || element?.getAttribute?.('content')
        || element?.getAttribute?.('datetime')
        || '';
      if (value.trim()) return value.trim();
    }
    return '';
  };
  const globalValue = (keys) => {
    for (const key of keys) {
      try {
        const value = window[key];
        if (value !== undefined && value !== null && String(value).trim()) {
          return String(value).trim();
        }
      } catch (_) {}
    }
    return '';
  };
  const meta = (keys) => {
    for (const key of keys) {
      const element = document.querySelector(
        `meta[property="${key}"], meta[name="${key}"]`
      );
      const value = element?.getAttribute('content') || '';
      if (value.trim()) return value.trim();
    }
    return '';
  };
  return {
    official: {
      title: pick(['#activity-name', '.rich_media_title', 'h1'])
        || globalValue(['msg_title']),
      content: pick(['#js_content', '.rich_media_content', 'article']),
      author: pick(['#js_name', '.account_nickname_inner', '.rich_media_meta_nickname'])
        || globalValue(['nickname']),
      authorId: globalValue(['user_name', 'fakeid', 'account_name']),
      accountUin: globalValue(['appuin', 'uin']),
      published: pick(['#publish_time', 'em#publish_time'])
        || globalValue(['ct', 'publish_time']),
    },
    video: {
      title: pick([
        '[class*="finder"] [class*="title"]',
        '[class*="video"] [class*="title"]',
        'h1'
      ]) || meta(['og:title', 'twitter:title']),
      content: pick([
        '[class*="finder"] [class*="desc"]',
        '[class*="video"] [class*="desc"]',
        '[class*="feed"] [class*="desc"]',
        '[class*="content"]'
      ]) || meta(['og:description', 'twitter:description', 'description']),
      author: pick([
        '[class*="finder"] [class*="nickname"]',
        '[class*="nickname"]',
        '[class*="author"] [class*="name"]'
      ]),
      authorId: document.querySelector('[data-finder-username]')?.getAttribute('data-finder-username')
        || document.querySelector('[data-username]')?.getAttribute('data-username')
        || '',
      authorUrl: document.querySelector(
        '[class*="nickname"] a[href], [class*="avatar"] a[href]'
      )?.href || '',
      published: pick([
        'time',
        '[class*="publish"] [class*="time"]',
        '[class*="create"] [class*="time"]'
      ])
    }
  };
}
"""

_WECHAT_VIDEO_GLOBALS_SCRIPT = r"""
() => {
  const pick = (value) => {
    try { return value ? JSON.stringify(value) : null; } catch (error) { return null; }
  };
  const w = window;
  for (const key of [
    '__feedInfo__', '_feedInfo', '__feed_info__', '__INITIAL_STATE__',
    '__INITIAL_DATA__', '__wxData__', '__wx_data__', '__wxStore__', '__wx_store__'
  ]) {
    const text = pick(w[key]);
    if (text) return text;
  }
  return null;
}
"""

# 空壳/中间页常见的假文案；命中时不得作为字段证据。
_SHELL_TEXTS = frozenset(
    {
        "微信视频号",
        "视频号",
        "微信",
        "wechat",
        "weixin",
        "wechatchannels",
        "channels",
        "wechat channels",
    }
)

_VIDEO_CONTENT_KEYS = (
    "description",
    "desc",
    "content",
    "caption",
    "objectDesc",
    "object_desc",
    "videoDescription",
    "video_description",
)
_VIDEO_TITLE_KEYS = ("title", "objectTitle", "object_title", "videoTitle")
_VIDEO_AUTHOR_CONTAINER_KEYS = (
    "finderUser",
    "finder_user",
    "contact",
    "author",
    "creator",
    "user",
    "owner",
)
_VIDEO_AUTHOR_NAME_KEYS = (
    "nickname",
    "nickName",
    "nick_name",
    "authorName",
    "author_name",
    "displayName",
    "display_name",
)
_VIDEO_AUTHOR_ID_KEYS = (
    "username",
    "userName",
    "user_name",
    "finderUsername",
    "finder_username",
    "contactUsername",
    "contact_username",
)


class WechatExtractor:
    platform_keys = ("wechat_official", "wechat_video")

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        if definition.key == "wechat_official":
            return await self._official(page, document)
        if definition.key == "wechat_video":
            return await self._video(page, document)
        return None

    async def _official(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> PageData | None:
        probe = await evaluate_value(page, _WECHAT_PAGE_PROBE)
        values = probe.get("official") if isinstance(probe, Mapping) else None
        values = values if isinstance(values, Mapping) else {}
        data = PageData(final_url=document.url)
        author_id = _clean_identifier(values.get("authorId"))
        applied = apply_json_fields(
            data,
            {
                "title": _clean(values.get("title")),
                "content_text": _clean(values.get("content")),
                "author_name": _clean(values.get("author")),
                "author_id": author_id,
                "account_uin": _clean_identifier(values.get("accountUin")),
                "published_at_dt": epoch_to_datetime(
                    _plausible_epoch(values.get("published"))
                ),
                "published_at_raw": (
                    _clean(values.get("published"))
                    if _plausible_epoch(values.get("published")) is None
                    else None
                ),
            },
            source=ExtractionSource.PLATFORM_DOM,
        )
        profile = _official_profile_url(document.url)
        if profile:
            applied += apply_json_fields(
                data,
                {"author_url": profile},
                source=ExtractionSource.DERIVED_URL,
            )
        return data if applied and found_any(data, "title", "content_text") else None

    async def _video(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> PageData | None:
        data = PageData(final_url=document.url)
        applied = 0
        # 页面全局 hydration 对象（视频号 web 端常见 __feedInfo__ 等）与
        # 网络/内嵌载荷一起参与最佳候选评分。
        globals_payload = await evaluate_json(page, _WECHAT_VIDEO_GLOBALS_SCRIPT)
        candidate, source = _best_video_candidate(
            document,
            extra_payloads=(globals_payload,) if globals_payload is not None else (),
        )
        if candidate is not None:
            author = _video_author(candidate)
            content = text_at(candidate, _VIDEO_CONTENT_KEYS)
            title = text_at(candidate, _VIDEO_TITLE_KEYS) or content
            author_id = text_at(author, _VIDEO_AUTHOR_ID_KEYS, max_chars=256)
            applied += apply_json_fields(
                data,
                {
                    "title": title,
                    "content_text": content or title,
                    "author_name": text_at(
                        author,
                        _VIDEO_AUTHOR_NAME_KEYS,
                        max_chars=256,
                    ),
                    "author_id": author_id,
                    "author_url": _video_author_url(author, author_id),
                    "published_at_dt": epoch_to_datetime(
                        epoch_at(
                            candidate,
                            (
                                "createTime",
                                "create_time",
                                "createtime",
                                "publishTime",
                                "publish_time",
                                "timestamp",
                            ),
                        )
                    ),
                },
                source=source or ExtractionSource.EMBEDDED_JSON,
            )

        probe = await evaluate_value(page, _WECHAT_PAGE_PROBE)
        values = probe.get("video") if isinstance(probe, Mapping) else None
        values = values if isinstance(values, Mapping) else {}
        if values:
            applied += apply_json_fields(
                data,
                {
                    "title": _not_shell(_clean(values.get("title"))),
                    "content_text": _not_shell(_clean(values.get("content"))),
                    "author_name": _clean(values.get("author")),
                    "author_id": _clean_identifier(values.get("authorId")),
                    "author_url": _clean_url(values.get("authorUrl")),
                    "published_at_raw": _clean(values.get("published")),
                },
                source=ExtractionSource.PLATFORM_DOM,
            )

        # OpenGraph values are useful on the short /sph/ landing page even
        # before the client application finishes hydrating.  Shell pages only
        # carry boilerplate like "微信视频号" — never accept those as fields.
        meta_title = _not_shell(_meta(document, "og:title", "twitter:title"))
        meta_desc = _not_shell(
            _meta(
                document,
                "og:description",
                "twitter:description",
                "description",
            )
        )
        applied += apply_json_fields(
            data,
            {
                "title": meta_title,
                "content_text": meta_desc or meta_title,
            },
            source=ExtractionSource.META,
        )
        return data if applied and found_any(data, "content_text", "title") else None


def _not_shell(text: str | None) -> str | None:
    """Reject boilerplate shell strings masquerading as page fields."""

    if not text:
        return None
    compact = " ".join(text.split()).casefold()
    return None if compact in _SHELL_TEXTS else text


def _best_video_candidate(
    document: RenderedDocument,
    *,
    extra_payloads: tuple[Any, ...] = (),
) -> tuple[Mapping[str, Any] | None, ExtractionSource | None]:
    best: tuple[int, Mapping[str, Any], ExtractionSource] | None = None
    payloads = (
        *((payload, ExtractionSource.EMBEDDED_JSON) for payload in extra_payloads),
        *((payload, ExtractionSource.NETWORK_JSON) for payload in document.network_payloads),
        *((payload, ExtractionSource.EMBEDDED_JSON) for payload in document.embedded_payloads),
    )
    for payload, source in payloads:
        for node in iter_mappings(payload, max_nodes=4_000):
            content = text_at(node, (*_VIDEO_CONTENT_KEYS, *_VIDEO_TITLE_KEYS))
            if not content:
                continue
            author = _video_author(node)
            author_name = text_at(author, _VIDEO_AUTHOR_NAME_KEYS, max_chars=256)
            author_id = text_at(author, _VIDEO_AUTHOR_ID_KEYS, max_chars=256)
            score = min(len(content), 500)
            if author_name:
                score += 500
            if author_id:
                score += 250
            if any(key in node for key in ("finderObject", "finder_object", "objectDesc")):
                score += 200
            if epoch_at(
                node,
                ("createTime", "create_time", "createtime", "publishTime"),
            ):
                score += 100
            if best is None or score > best[0]:
                best = (score, node, source)
    return (best[1], best[2]) if best is not None else (None, None)


def _video_author(node: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in _VIDEO_AUTHOR_CONTAINER_KEYS:
        nested = node.get(key)
        if isinstance(nested, Mapping):
            return nested
    finder_object = node.get("finderObject") or node.get("finder_object")
    if isinstance(finder_object, Mapping):
        for key in _VIDEO_AUTHOR_CONTAINER_KEYS:
            nested = finder_object.get(key)
            if isinstance(nested, Mapping):
                return nested
        return finder_object
    return node


def _video_author_url(
    author: Mapping[str, Any],
    author_id: str | None,
) -> str | None:
    direct = text_at(
        author,
        ("authorUrl", "author_url", "profileUrl", "profile_url", "url"),
        max_chars=4_096,
    )
    if direct and direct.startswith(("http://", "https://")):
        return direct
    # The public share surface does not expose a stable, documented profile
    # route for every account.  Do not invent one from an internal username.
    return None


def _official_profile_url(url: str) -> str | None:
    biz = parse_qs(urlsplit(url).query).get("__biz", [""])[0].strip()
    if not biz:
        return None
    return (
        "https://mp.weixin.qq.com/mp/profile_ext"
        f"?action=home&__biz={quote(biz, safe='=')}&scene=124#wechat_redirect"
    )


def _meta(document: RenderedDocument, *keys: str) -> str | None:
    return next((document.meta[key] for key in keys if document.meta.get(key)), None)


def _plausible_epoch(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1_000
    return number if 946_684_800 <= number <= 4_102_444_800 else None


def _clean(value: Any) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _clean_identifier(value: Any) -> str | None:
    text = _clean(value)
    if not text or len(text) > 256:
        return None
    return text


def _clean_url(value: Any) -> str | None:
    text = _clean(value)
    if text and text.startswith(("http://", "https://")):
        return text
    return None


register(WechatExtractor())
