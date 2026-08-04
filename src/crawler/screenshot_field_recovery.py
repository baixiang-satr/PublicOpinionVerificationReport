"""Recover missing title/content/time from the already validated page screenshot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Callable

from src.domain.models import ExtractionSource, PageData
from src.crawler.platforms.baijiahao import is_video_landing_url
from src.utils.ocr import extract_text_from_images
from src.utils.time_utils import parse_web_published_at


_NOISY_TITLES = {
    "prefetch",
    "untitled",
    "document",
    "page",
    "home",
    "首页",
    "商品详情",
    "视频详情",
    "文章详情",
}
_TITLE_REJECT_MARKERS = (
    "打开app",
    "立即打开",
    "去app",
    "扫码",
    "登录",
    "注册",
    "购物车",
    "客服",
    "分享",
    "关注",
    "copyright",
)


def needs_screenshot_field_recovery(page: PageData) -> bool:
    # A video landing page can visibly contain download chrome, recommendation
    # cards and unrelated OCR-able player frames.  Without an exact nid match,
    # screenshot OCR would merely reintroduce the fields discarded by the
    # dedicated extractor as unverified.
    if is_video_landing_url(page.final_url):
        return False
    return (
        _noisy_title(page.title)
        or not page.content_text
        or page.published_at is None
    )


def recover_fields_from_screenshot(
    page: PageData,
    screenshot: Path,
    *,
    summary_max_chars: int,
    confidence_threshold: float,
    ocr: Callable[..., str] = extract_text_from_images,
) -> None:
    """Fill only missing/noisy fields; never overwrite trustworthy extracted values."""

    if not needs_screenshot_field_recovery(page):
        return
    text = ocr(
        [screenshot],
        confidence_threshold=confidence_threshold,
    )
    if not text or text == "无文字":
        return
    recover_fields_from_ocr_text(
        page,
        text,
        summary_max_chars=summary_max_chars,
    )


def recover_fields_from_ocr_text(
    page: PageData,
    text: str,
    *,
    summary_max_chars: int,
) -> None:
    """Recover missing fields from OCR text already produced by a worker."""

    if not text:
        return
    if _noisy_title(page.title):
        title = _best_title_line(text)
        if title:
            page.title = title
            page.field_sources["title"] = ExtractionSource.OCR
    if not page.content_text:
        page.content_text = text
        page.field_sources["content_text"] = ExtractionSource.OCR
    if page.published_at is None:
        published = _first_published_at(text)
        if published is not None:
            page.published_at = published
            page.field_sources["published_at"] = ExtractionSource.OCR
    content = page.content_text or ""
    page.content_summary = content[:summary_max_chars]
    page.summary_truncated = len(content) > summary_max_chars


def _noisy_title(value: str | None) -> bool:
    return not value or value.strip().casefold() in _NOISY_TITLES


def _best_title_line(text: str) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    for index, raw in enumerate(text.splitlines()[:80]):
        line = re.sub(r"\s+", " ", raw).strip()
        compact = line.casefold().replace(" ", "")
        if not 4 <= len(line) <= 100:
            continue
        if any(marker in compact for marker in _TITLE_REJECT_MARKERS):
            continue
        if re.fullmatch(r"[\d\s:./\-年月日￥¥?]+", line):
            continue
        if line.startswith(("http://", "https://")):
            continue
        useful = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", line))
        score = min(useful, 70) - min(index, 20)
        if re.search(r"[。！？!?]$", line) and len(line) > 60:
            score -= 20
        candidates.append((score, -index, line))
    return max(candidates)[2] if candidates else None


def _first_published_at(text: str) -> datetime | None:
    for line in text.splitlines():
        if not re.search(r"(?:19|20)\d{2}[年./\-]\d{1,2}", line):
            continue
        parsed = parse_web_published_at(line)
        if parsed is not None:
            return parsed
    return None
