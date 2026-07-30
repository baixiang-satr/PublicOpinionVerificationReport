"""Additive official-API fallback helpers for dedicated platform extractors.

When the rendered page is blocked (login wall, HTTP 403, empty shell) the
embedded/network JSON pipelines find nothing.  Several platforms, however,
still answer *public* JSON endpoints when the request rides the existing
browser session (same cookies, same TLS fingerprint).  This module adds that
extra fetch layer on top of the current extractors — it never replaces the
existing paths, it only supplies a payload when they found nothing:

- Douyin:  ``iesdouyin.com`` share/iteminfo endpoints (no signing required).
- Weibo:   ``m.weibo.cn/statuses/show`` mobile JSON (visitor session works).
- Zhihu:   ``www.zhihu.com/api/v4`` question/answer JSON.

Every helper is read-only, bounded (timeout + byte cap), and never raises:
any failure simply yields ``None`` so the caller falls through to the
generic pipeline exactly as before.  Set ``POR_DISABLE_API_ASSIST=1`` to
turn the whole layer off.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 2_000_000
_TIMEOUT_MS = 15_000

_JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def api_assist_enabled() -> bool:
    """Whether the additive API fallback layer is active (default: on)."""

    return os.environ.get("POR_DISABLE_API_ASSIST", "").strip() not in {"1", "true", "yes"}


async def fetch_json(
    page: Any,
    url: str,
    *,
    referer: str | None = None,
    mobile: bool = False,
) -> Any | None:
    """GET *url* through the page's browser context and return parsed JSON.

    Uses ``page.context.request`` so the call inherits session cookies and
    the browser TLS fingerprint.  Returns ``None`` on any failure or when
    the body is not JSON within the size cap.
    """

    if not api_assist_enabled():
        return None
    context = getattr(page, "context", None)
    request = getattr(context, "request", None)
    get = getattr(request, "get", None)
    if get is None:
        return None
    headers = dict(_JSON_HEADERS)
    if referer:
        headers["Referer"] = referer
    if mobile:
        headers["User-Agent"] = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
            "Mobile/15E148 Safari/604.1"
        )
    response = None
    try:
        response = await get(url, headers=headers, timeout=_TIMEOUT_MS, fail_on_status_code=False)
        status = int(getattr(response, "status", 0) or 0)
        if status < 200 or status >= 300:
            logger.debug("api_assist %s -> HTTP %s", url, status)
            return None
        body = await response.body()
        if not body or len(body) > _MAX_BODY_BYTES:
            return None
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception as error:
        logger.debug("api_assist fetch failed for %s: %s", url, error)
        return None
    finally:
        if response is not None:
            try:
                await response.dispose()
            except Exception:
                pass


# ── Douyin ──────────────────────────────────────────────────────────


def douyin_aweme_id(url: str) -> str | None:
    """Extract the numeric aweme id from a douyin video/note URL."""

    match = re.search(r"/(?:video|note)/(\d{6,})", url)
    return match.group(1) if match else None


async def douyin_aweme_detail(page: Any, aweme_id: str) -> Mapping[str, Any] | None:
    """Fetch the aweme detail JSON through public iesdouyin endpoints."""

    api_url = (
        "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"
        f"?item_ids={quote(aweme_id)}&dytk="
    )
    payload = await fetch_json(page, api_url, referer="https://www.iesdouyin.com/")
    detail = _first_item(payload)
    if detail is not None:
        return detail

    share_url = f"https://www.iesdouyin.com/share/video/{quote(aweme_id)}/"
    payload = await fetch_json(page, share_url, referer="https://www.iesdouyin.com/")
    return _first_item(payload)


def _first_item(payload: Any) -> Mapping[str, Any] | None:
    """Locate ``item_list[0]`` in iteminfo or share-page ``_ROUTER_DATA`` JSON."""

    if not isinstance(payload, Mapping):
        return None
    queue: list[Any] = [payload]
    seen = 0
    while queue and seen < 2_000:
        current = queue.pop(0)
        if isinstance(current, Mapping):
            seen += 1
            items = current.get("item_list")
            if isinstance(items, list) and items and isinstance(items[0], Mapping):
                candidate = items[0]
                if "desc" in candidate or "author" in candidate:
                    return candidate
            queue.extend(current.values())
        elif isinstance(current, (list, tuple)):
            queue.extend(current)
    return None


# ── Weibo ───────────────────────────────────────────────────────────


def weibo_bid(url: str) -> str | None:
    """Extract the base62 mblog id (bid) from a weibo detail URL."""

    match = re.search(r"weibo\.com/\d+/([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/(?:detail|status)/([A-Za-z0-9]+)", url)
    return match.group(1) if match else None


async def weibo_mblog(page: Any, bid: str) -> Mapping[str, Any] | None:
    """Fetch mblog JSON from the public mobile endpoint ``m.weibo.cn``."""

    api_url = f"https://m.weibo.cn/statuses/show?id={quote(bid)}"
    payload = await fetch_json(
        page,
        api_url,
        referer=f"https://m.weibo.cn/detail/{quote(bid)}",
        mobile=True,
    )
    if isinstance(payload, Mapping) and (
        "text" in payload or "text_raw" in payload
    ):
        return payload
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
        data = payload["data"]
        if "text" in data or "text_raw" in data:
            return data
    return None


# ── Zhihu ───────────────────────────────────────────────────────────


def zhihu_ids(url: str) -> tuple[str | None, str | None]:
    """Return ``(question_id, answer_id)`` parsed from a zhihu URL."""

    question = re.search(r"/question/(\d+)", url)
    answer = re.search(r"/answer/(\d+)", url)
    return (
        question.group(1) if question else None,
        answer.group(1) if answer else None,
    )


async def zhihu_answer(page: Any, answer_id: str) -> Mapping[str, Any] | None:
    """Fetch one answer through the public ``api/v4`` JSON endpoint."""

    include = quote(
        "content,excerpt,created_time,updated_time,author,question", safe=","
    )
    api_url = f"https://www.zhihu.com/api/v4/answers/{quote(answer_id)}?include={include}"
    payload = await fetch_json(page, api_url, referer="https://www.zhihu.com/")
    if isinstance(payload, Mapping) and ("content" in payload or "excerpt" in payload):
        return payload
    return None


async def zhihu_question(page: Any, question_id: str) -> Mapping[str, Any] | None:
    """Fetch one question through the public ``api/v4`` JSON endpoint."""

    include = quote("detail,excerpt,created,updated,author", safe=",")
    api_url = (
        f"https://www.zhihu.com/api/v4/questions/{quote(question_id)}?include={include}"
    )
    payload = await fetch_json(page, api_url, referer="https://www.zhihu.com/")
    if isinstance(payload, Mapping) and "title" in payload:
        return payload
    return None
