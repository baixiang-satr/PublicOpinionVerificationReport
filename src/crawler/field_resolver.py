"""Choose trustworthy field candidates across JSON, DOM, metadata and OCR."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from src.domain.models import ExtractionSource, PageData


_SOURCE_CONFIDENCE = {
    ExtractionSource.MANUAL: 1.0,
    ExtractionSource.NETWORK_JSON: 0.98,
    ExtractionSource.EMBEDDED_JSON: 0.94,
    ExtractionSource.JSON_LD: 0.90,
    ExtractionSource.PLATFORM_DOM: 0.86,
    ExtractionSource.META: 0.72,
    ExtractionSource.OCR: 0.68,
    ExtractionSource.DERIVED_URL: 0.66,
    ExtractionSource.GENERIC_DOM: 0.54,
    ExtractionSource.VISIBLE_TEXT: 0.42,
    ExtractionSource.NICKNAME_FALLBACK: 0.25,
    ExtractionSource.SYSTEM_MARKER: 0.20,
}
_NOISY_EXACT = {
    "prefetch",
    "untitled",
    "document",
    "page",
    "home",
    "首页",
    "登录",
    "注册",
    "商品详情",
    "视频详情",
    "文章详情",
    "帮助中心",
}
_TITLE_MARKERS = (
    "登录",
    "注册",
    "安全验证",
    "访问验证",
    "页面不存在",
    "内容不存在",
    "打开app",
)
_AUTHOR_EXACT = {
    "首页",
    "登录",
    "点击登录",
    "未登录",
    "作者",
    "用户",
    "account",
    "profile",
}


def consider_field(
    page: PageData,
    field: str,
    value: Any,
    source: ExtractionSource,
) -> bool:
    normalized = _normalize(field, value)
    if normalized is None:
        return False
    confidence = _confidence(field, normalized, source)
    if confidence <= 0:
        return False
    current = getattr(page, field)
    current_source = page.field_sources.get(field)
    current_confidence = page.field_confidences.get(
        field,
        _SOURCE_CONFIDENCE.get(current_source, 0.0),
    )
    if (
        field == "content_text"
        and current
        and _should_preserve_substantive_content(
            str(current),
            current_source,
            normalized,
        )
    ):
        confidence -= 0.40
    if current and confidence <= current_confidence:
        return False
    setattr(page, field, normalized)
    page.field_sources[field] = source
    page.field_confidences[field] = round(confidence, 4)
    return True


def merge_page_data(primary: PageData, fallback: PageData) -> PageData:
    for field in (
        "title",
        "content_text",
        "author_name",
        "author_id",
        "author_url",
        "account_uin",
        "store_name",
        "published_at_raw",
    ):
        source = fallback.field_sources.get(field)
        if source is not None:
            consider_field(primary, field, getattr(fallback, field), source)
    if primary.published_at is None and fallback.published_at is not None:
        primary.published_at = fallback.published_at
        source = fallback.field_sources.get(
            "published_at",
            fallback.field_sources.get("published_at_raw"),
        )
        if source is not None:
            primary.field_sources["published_at"] = source
            primary.field_confidences["published_at"] = (
                fallback.field_confidences.get("published_at")
                or fallback.field_confidences.get("published_at_raw")
                or _SOURCE_CONFIDENCE.get(source, 0.0)
            )
    primary.image_urls = list(
        dict.fromkeys([*primary.image_urls, *fallback.image_urls])
    )
    if primary.text_type_hint == "正文":
        primary.text_type_hint = fallback.text_type_hint
    return primary


def _normalize(field: str, value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("\u200b", "").replace("\ufeff", "").strip()
    if not text:
        return None
    if field == "author_name":
        text = re.sub(r"\s+", " ", text.splitlines()[0]).strip()
        compact = text.casefold().replace(" ", "")
        if (
            not text
            or len(text) > 100
            or compact in _AUTHOR_EXACT
            or re.search(
                r"(?:19|20)\d{2}[-/.年]\d{1,2}.*(?:来源|source)",
                text,
                re.I,
            )
        ):
            return None
    if field == "title":
        text = re.sub(r"\s+", " ", text)
        compact = text.casefold().replace(" ", "")
        if (
            compact in _NOISY_EXACT
            or len(text) < 2
            or any(marker in compact for marker in _TITLE_MARKERS)
        ):
            return None
    if field == "author_url":
        parsed = urlsplit(text)
        if parsed.scheme:
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                return None
        elif not (
            text.startswith("/")
            or text.startswith("./")
            or text.startswith("../")
        ):
            return None
    return text


def _confidence(
    field: str,
    value: str,
    source: ExtractionSource,
) -> float:
    score = _SOURCE_CONFIDENCE[source]
    if field == "content_text":
        compact_length = len(re.sub(r"\s+", "", value))
        if compact_length < 8:
            score -= (
                0.15
                if source
                in {
                    ExtractionSource.NETWORK_JSON,
                    ExtractionSource.EMBEDDED_JSON,
                    ExtractionSource.JSON_LD,
                    ExtractionSource.PLATFORM_DOM,
                }
                else 0.40
            )
        elif compact_length < 30:
            score -= 0.18
        elif compact_length >= 300:
            score += 0.03
        if _looks_like_platform_shell(value):
            score -= 0.45
    elif field == "title":
        if 6 <= len(value) <= 80:
            score += 0.03
        if len(value) > 160:
            score -= 0.25
    elif field == "author_name" and len(value) > 40:
        score -= 0.20
    return max(0.0, min(1.0, score))


def _looks_like_platform_shell(value: str) -> bool:
    compact = value.casefold().replace(" ", "")
    markers = (
        "登录后查看更多",
        "请登录",
        "下载客户端",
        "返回首页",
        "copyright",
    )
    return len(compact) < 240 and any(marker in compact for marker in markers)


def _should_preserve_substantive_content(
    current: str,
    current_source: ExtractionSource | None,
    candidate: str,
) -> bool:
    if current_source not in {
        ExtractionSource.PLATFORM_DOM,
        ExtractionSource.EMBEDDED_JSON,
        ExtractionSource.JSON_LD,
    }:
        return False
    current_length = len(re.sub(r"\s+", "", current))
    candidate_length = len(re.sub(r"\s+", "", candidate))
    return (
        current_length >= 500
        and candidate_length < current_length * 0.40
    )
