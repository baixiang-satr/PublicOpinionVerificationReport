"""Xiaohongshu (RED) note extractor.

Current note pages render the requested note from a JSON-like
``window.__INITIAL_STATE__`` assignment.  It is not valid JSON because the
payload may contain bare ``undefined`` values, and the application removes
the global after hydration.  The generic collector therefore cannot rely on
``window.__INITIAL_STATE__`` being present.

This extractor reads the original script node, replaces only bare
``undefined`` tokens outside quoted strings, and selects the note whose ID
matches the URL.  Exact-ID matching is important because the same page also
contains recommendations and comments.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit

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

_INITIAL_STATE_NOTE_SCRIPT = r"""
() => {
  const match = location.pathname.match(
    /\/(?:explore|discovery\/item)\/([^/?#]+)/
  );
  const wantedId = match ? decodeURIComponent(match[1]) : '';
  if (!wantedId) return null;

  const unwrap = (value) => {
    if (
      value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      Object.keys(value).length === 1 &&
      Object.prototype.hasOwnProperty.call(value, 'value')
    ) {
      return value.value;
    }
    return value;
  };
  const noteFromState = (state) => {
    const noteRoot = unwrap(state?.note);
    const detailMap = unwrap(noteRoot?.noteDetailMap);
    const entry = detailMap && typeof detailMap === 'object'
      ? unwrap(detailMap[wantedId])
      : null;
    const note = unwrap(entry?.note);
    return note && typeof note === 'object' ? note : null;
  };

  try {
    const live = noteFromState(window.__INITIAL_STATE__);
    if (live) return live;
  } catch (_) {}

  const marker = 'window.__INITIAL_STATE__=';
  const nodes = Array.from(document.scripts).slice(0, 80);
  const node = nodes.find((candidate) =>
    (candidate.textContent || '').includes(marker)
  );
  if (!node) return null;
  const source = node.textContent || '';
  const markerIndex = source.indexOf(marker);
  if (markerIndex < 0) return null;
  let raw = source.slice(markerIndex + marker.length).trim();
  raw = raw.replace(/;\s*$/, '');

  // The SSR serializer emits JavaScript's bare `undefined`.  Replace that
  // token only outside quoted strings so user-authored text is unchanged.
  let normalized = '';
  let inString = false;
  let escaped = false;
  for (let index = 0; index < raw.length; index += 1) {
    const character = raw[index];
    if (inString) {
      normalized += character;
      if (escaped) {
        escaped = false;
      } else if (character === '\\') {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
      continue;
    }
    if (character === '"') {
      inString = true;
      normalized += character;
      continue;
    }
    if (raw.startsWith('undefined', index)) {
      const before = index > 0 ? raw[index - 1] : '';
      const after = raw[index + 9] || '';
      const identifier = /[A-Za-z0-9_$]/;
      if (!identifier.test(before) && !identifier.test(after)) {
        normalized += 'null';
        index += 8;
        continue;
      }
    }
    normalized += character;
  }

  try {
    return noteFromState(JSON.parse(normalized));
  } catch (_) {
    return null;
  }
}
"""

_NOTE_DOM_PROBE = r"""
() => {
  const pick = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const value = element ? (element.innerText || element.textContent || '').trim() : '';
      if (value) return value;
    }
    return '';
  };
  const anchor = document.querySelector(
    ".author-container a[href*='/user/profile/'], "
    + ".author-wrapper a[href*='/user/profile/'], "
    + "a[href*='/user/profile/']"
  );
  return {
    title: pick(["#detail-title", ".note-content .title"]),
    content: pick(["#detail-desc", ".note-content .desc"]),
    author: pick([
      ".author-container .username",
      ".author-wrapper .name",
      "span.username",
      ".user .name",
      ".author .name"
    ]),
    authorUrl: anchor ? anchor.href : '',
    published: pick([".note-content .date", ".date", ".publish-time"])
  };
}
"""

_NOTE_ID_PATTERN = re.compile(
    r"/(?:explore|discovery/item)/([^/?#]+)",
    re.IGNORECASE,
)


class XiaohongshuExtractor:
    platform_keys = ("xiaohongshu",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        note, note_source = await self._find_note(page, document)
        data = PageData(final_url=document.url)
        applied = 0
        if note is not None:
            user = note.get("user")
            user = user if isinstance(user, Mapping) else {}
            user_id = text_at(user, ("userId", "user_id", "id"))
            public_account_id = text_at(
                user,
                ("redId", "red_id", "redID"),
            )
            author_url = _author_url(user, user_id)
            applied = apply_json_fields(
                data,
                {
                    "title": text_at(note, ("title",)),
                    "content_text": _clean_note_desc(
                        text_at(note, ("desc", "content"))
                    ),
                    "author_name": text_at(
                        user,
                        ("nickname", "nickName", "name"),
                    ),
                    # ``userId`` is an internal profile-route identifier.
                    # The workbook's 用户账号 column expects the public
                    # 小红书号 whenever the payload provides it.
                    "author_id": public_account_id or user_id,
                    "author_url": author_url,
                    "published_at_dt": epoch_to_datetime(
                        epoch_at(
                            note,
                            (
                                "time",
                                "publishTime",
                                "publish_time",
                                "createTime",
                                "create_time",
                            ),
                        )
                    ),
                },
                source=note_source or ExtractionSource.EMBEDDED_JSON,
            )
            data.image_urls = _note_image_urls(note)

        # The target note's own DOM is a safe additive fallback.  It also
        # preserves the platform-generated xsec token on the author link when
        # the embedded user node is incomplete.
        if (
            not data.title
            or not data.content_text
            or not data.author_name
            or not data.author_url
        ):
            applied += await self._from_dom(data, page)
        return data if applied and found_any(data, "content_text", "title") else None

    async def _from_dom(self, data: PageData, page: Any) -> int:
        probe = await evaluate_value(page, _NOTE_DOM_PROBE)
        if not isinstance(probe, Mapping):
            return 0
        author_url = probe.get("authorUrl")
        author_id = _author_id_from_url(author_url)
        return apply_json_fields(
            data,
            {
                "title": probe.get("title") or None,
                "content_text": probe.get("content") or None,
                "author_name": probe.get("author") or None,
                "author_url": author_url or None,
                "author_id": author_id,
                "published_at_raw": probe.get("published") or None,
            },
            source=ExtractionSource.PLATFORM_DOM,
        )

    async def _find_note(
        self,
        page: Any,
        document: RenderedDocument,
    ) -> tuple[Mapping[str, Any] | None, ExtractionSource | None]:
        wanted_id = xiaohongshu_note_id(document.url)
        if wanted_id:
            initial_note = await evaluate_json(page, _INITIAL_STATE_NOTE_SCRIPT)
            if isinstance(initial_note, Mapping) and _id_matches(
                initial_note,
                wanted_id,
            ):
                return initial_note, ExtractionSource.EMBEDDED_JSON

        for payload, source in (
            *(
                (item, ExtractionSource.NETWORK_JSON)
                for item in document.network_payloads
            ),
            *(
                (item, ExtractionSource.EMBEDDED_JSON)
                for item in document.embedded_payloads
            ),
        ):
            note = _note_node(payload, wanted_id=wanted_id)
            if note is not None:
                return note, source
        return None, None


def xiaohongshu_note_id(url: str) -> str | None:
    """Return the note ID carried by an explore/discovery URL."""

    path = urlsplit(url).path
    match = _NOTE_ID_PATTERN.search(path)
    if match is None:
        return None
    value = unquote(match.group(1)).strip()
    return value or None


def _note_node(
    payload: Any,
    wanted_id: str | None = None,
) -> Mapping[str, Any] | None:
    # Prefer the canonical noteDetailMap and use its key as strong identity
    # evidence even when the nested note omits noteId.
    for mapping in iter_mappings(payload):
        detail_map = mapping.get("noteDetailMap")
        if not isinstance(detail_map, Mapping):
            continue
        if wanted_id:
            entry = detail_map.get(wanted_id)
            if isinstance(entry, Mapping):
                note = entry.get("note")
                if isinstance(note, Mapping) and _looks_like_note(note):
                    return note
            continue
        for entry in detail_map.values():
            if isinstance(entry, Mapping):
                note = entry.get("note")
                if isinstance(note, Mapping) and _looks_like_note(note):
                    return note

    # Current detail APIs may expose note_card/noteCard or the note mapping
    # directly.  With a URL ID, never accept an anonymous/recommended note.
    for mapping in iter_mappings(payload):
        candidates = [mapping]
        for key in ("note", "note_card", "noteCard"):
            nested = mapping.get(key)
            if isinstance(nested, Mapping):
                candidates.append(nested)
        for candidate in candidates:
            if not _looks_like_note(candidate):
                continue
            if _id_matches(candidate, wanted_id):
                return candidate
    return None


def _looks_like_note(note: Mapping[str, Any]) -> bool:
    return (
        ("desc" in note or "content" in note)
        and (
            "title" in note
            or "time" in note
            or "publishTime" in note
            or "user" in note
        )
    )


def _id_matches(note: Mapping[str, Any], wanted_id: str | None) -> bool:
    if not wanted_id:
        return True
    candidate = text_at(note, ("noteId", "note_id", "id"), max_chars=128)
    return candidate == wanted_id


def _author_url(user: Mapping[str, Any], user_id: str | None) -> str | None:
    if not user_id:
        return None
    base = f"https://www.xiaohongshu.com/user/profile/{quote(user_id, safe='')}"
    token = text_at(user, ("xsecToken", "xsec_token"), max_chars=1_024)
    if not token:
        return base
    return (
        f"{base}?xsec_token={quote(token, safe='')}"
        "&xsec_source=pc_note"
    )


def _author_id_from_url(value: Any) -> str | None:
    if not isinstance(value, str) or "/user/profile/" not in value:
        return None
    path = urlsplit(value).path
    candidate = path.rsplit("/user/profile/", 1)[-1].strip("/")
    return unquote(candidate) or None


def _clean_note_desc(value: str | None) -> str | None:
    if not value:
        return None
    # SSR uses ``#话题名[话题]#`` while the rendered page displays
    # ``#话题名``.  Normalize only this transport marker.
    return re.sub(
        r"#\s*([^#\n]{1,100}?)\s*\[话题\]\s*#",
        lambda match: f"#{match.group(1).strip()}",
        value,
    ).strip()


def _note_image_urls(note: Mapping[str, Any]) -> list[str]:
    images = note.get("imageList") or note.get("images")
    if not isinstance(images, (list, tuple)):
        return []
    urls: list[str] = []
    for image in images:
        if not isinstance(image, Mapping):
            continue
        selected = text_at(
            image,
            (
                "urlDefault",
                "url_default",
                "urlPre",
                "url_pre",
                "url",
            ),
            max_chars=8_192,
        )
        if not selected:
            info_list = image.get("infoList") or image.get("info_list")
            if isinstance(info_list, (list, tuple)):
                for info in info_list:
                    if not isinstance(info, Mapping):
                        continue
                    selected = text_at(
                        info,
                        ("url", "urlDefault", "url_default"),
                        max_chars=8_192,
                    )
                    if selected:
                        break
        if selected and selected.startswith(("http://", "https://", "//")):
            urls.append(f"https:{selected}" if selected.startswith("//") else selected)
    return list(dict.fromkeys(urls))


register(XiaohongshuExtractor())
