"""URL-scoped Sohu Video extractor.

Sohu's current ``tv.sohu.com/v/<base64>.html`` pages expose the target video
as page globals (``vid``, ``_videoInfo`` and ``wm_username``).  Generic DOM
selectors are unsafe on these pages: the first ``h1`` is the Sohu logo, the
first user link belongs to the global navigation, and broad time selectors
match the player's advertisement countdown.

The encoded route carries the target video ID, so this extractor validates
the page-global payload against that ID before accepting any fields.
"""
from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

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

_VIDEO_PROBE = r"""
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
  const info = (
    window._videoInfo &&
    typeof window._videoInfo === 'object' &&
    !Array.isArray(window._videoInfo)
  ) ? window._videoInfo : {};
  const authorAnchor = document.querySelector(
    ".jieshao a.jieshao-user[href], "
    + ".jieshao > a[href*='/user/'], "
    + "#infoplayer a.jieshao-user[href]"
  );
  const authorName = (
    typeof window.wm_username === 'string' ? window.wm_username.trim() : ''
  ) || text(authorAnchor);
  const uid = String(
    info.uid || window._uid || (
      authorAnchor
        ? ((authorAnchor.getAttribute('href') || '').match(/\/user\/(\d+)/) || [])[1]
        : ''
    ) || ''
  ).trim();
  const vid = String(info.vid || window.vid || '').trim();
  const authorUrl = authorAnchor
    ? authorAnchor.href
    : (uid ? `https://tv.sohu.com/user/${encodeURIComponent(uid)}` : '');
  return {
    embedded: Boolean(Object.keys(info).length || vid),
    vid,
    uid,
    title: String(info.title || '').trim() || meta('og:title'),
    description: meta('description') || meta('og:description'),
    author: authorName,
    authorUrl,
    published: String(
      info.publishTime || info.uploadTime || info.publish_time || ''
    ).trim()
  };
}
"""

_ENCODED_ROUTE_RE = re.compile(r"^/v/(.+)\.html$", re.IGNORECASE)
_LEGACY_ROUTE_RE = re.compile(
    r"(?:^|/)us/(?P<uid>\d+)/(?P<vid>\d+)\.shtml$",
    re.IGNORECASE,
)
_DECODED_VIDEO_RE = re.compile(
    r"(?:^|/)(?:n)?(?P<vid>\d+)\.shtml$",
    re.IGNORECASE,
)


class SohuVideoExtractor:
    platform_keys = ("sohu_video",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        probe = await evaluate_value(page, _VIDEO_PROBE)
        if not isinstance(probe, Mapping):
            return None

        expected_uid, expected_vid = sohu_video_identity(document.url)
        candidate_uid = _clean(probe.get("uid"))
        candidate_vid = _clean(probe.get("vid"))
        if expected_vid and candidate_vid != expected_vid:
            return None
        if expected_uid and candidate_uid and candidate_uid != expected_uid:
            return None

        title = _clean(probe.get("title"))
        published_raw = _clean(probe.get("published"))
        description = _video_description(
            _clean(probe.get("description")),
            title,
        )
        source = (
            ExtractionSource.EMBEDDED_JSON
            if probe.get("embedded")
            else ExtractionSource.PLATFORM_DOM
        )
        data = PageData(final_url=document.url)
        applied = apply_json_fields(
            data,
            {
                "title": title,
                # Sohu UGC pages do not expose a separate video transcript.
                # Their page-scoped description commonly repeats the exact
                # video title, which is still truthful information content.
                "content_text": description or title,
                "author_name": _clean(probe.get("author")),
                "author_id": candidate_uid,
                "author_url": _clean(probe.get("authorUrl")),
                "published_at_raw": published_raw,
                "published_at_dt": (
                    parse_web_published_at(published_raw)
                    if published_raw
                    else None
                ),
            },
            source=source,
        )
        return (
            data
            if applied and found_any(data, "title", "content_text", "author_name")
            else None
        )


def sohu_video_identity(url: str) -> tuple[str | None, str | None]:
    """Return ``(uploader_id, video_id)`` carried by a Sohu video URL."""

    path = unquote(urlsplit(url).path)
    legacy = _LEGACY_ROUTE_RE.search(path)
    if legacy:
        return legacy.group("uid"), legacy.group("vid")

    encoded_match = _ENCODED_ROUTE_RE.match(path)
    if encoded_match is None:
        return None, None
    candidate = unquote(encoded_match.group(1)).strip()
    try:
        padding = "=" * (-len(candidate) % 4)
        decoded = base64.b64decode(
            f"{candidate}{padding}",
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None, None

    legacy = _LEGACY_ROUTE_RE.search(decoded)
    if legacy:
        return legacy.group("uid"), legacy.group("vid")
    video = _DECODED_VIDEO_RE.search(decoded)
    return (None, video.group("vid")) if video else (None, None)


def _video_description(
    description: str | None,
    title: str | None,
) -> str | None:
    if not description:
        return None
    if not title:
        return description
    if description == title:
        return title
    if description.endswith(title):
        prefix = description[: -len(title)].strip()
        if len(prefix) <= 40 and prefix.endswith((":", "：")):
            return title
    return description


def _clean(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


register(SohuVideoExtractor())
