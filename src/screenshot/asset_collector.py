"""Collect validated page images without exposing failed files to export."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.config.settings import TaskConfig
from src.crawler.fetcher import AssetFetchError, ImageFetcher
from src.domain.models import TaskError


@dataclass(frozen=True)
class AssetCollectionResult:
    files: tuple[Path, ...] = ()
    errors: tuple[TaskError, ...] = ()


# Bounded parallelism for image downloads: sequential fetches let a few
# hanging URLs eat the whole optional-enrichment budget (regression: record
# 18 lost an otherwise successful baijiahao page to five sequential 30s
# image timeouts).
_DOWNLOAD_CONCURRENCY = 3

_MARK_SCRIPT = """(url) => {
    const candidates = Array.from(document.querySelectorAll('img'));
    const target = candidates.find(
      (img) => img.currentSrc === url || img.src === url
    );
    if (!target) return false;
    target.setAttribute('data-por-ocr-fallback', '1');
    return true;
}"""

_UNMARK_SCRIPT = """() => {
    for (const img of document.querySelectorAll('[data-por-ocr-fallback]')) {
      img.removeAttribute('data-por-ocr-fallback');
    }
    return true;
}"""


class AssetCollector:
    def __init__(self, config: TaskConfig, fetcher: ImageFetcher | None = None) -> None:
        self._config = config
        self._fetcher = fetcher or ImageFetcher()

    async def collect(
        self,
        page: Any,
        image_urls: list[str],
        evidence_id: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None = None,
    ) -> AssetCollectionResult:
        if self._config.max_images_per_record == 0:
            return AssetCollectionResult()

        candidates = list(dict.fromkeys(image_urls))[: self._config.max_images_per_record]
        semaphore = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)
        results: list[tuple[Path | None, TaskError | None]] = []

        async def worker(image_index: int, url: str) -> None:
            _raise_if_cancelled(cancel_event)
            async with semaphore:
                results.append(
                    (
                        await self._fetch_one(
                            page,
                            url,
                            evidence_id,
                            image_index,
                            output_dir,
                            cancel_event,
                        )
                    )
                )

        outcomes = await asyncio.gather(
            *(worker(image_index, url) for image_index, url in enumerate(candidates, start=1)),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        files = [path for path, _error in results if path is not None]
        errors = [error for _path, error in results if error is not None]
        return AssetCollectionResult(tuple(files), tuple(errors))

    async def _fetch_one(
        self,
        page: Any,
        url: str,
        evidence_id: int,
        image_index: int,
        output_dir: Path,
        cancel_event: asyncio.Event | None,
    ) -> tuple[Path | None, TaskError | None]:
        fetch_error: AssetFetchError | None = None
        try:
            path = await self._fetcher.fetch(
                page=page,
                url=url,
                output_dir=output_dir,
                evidence_id=evidence_id,
                image_index=image_index,
                max_bytes=self._config.max_image_bytes,
                timeout_seconds=self._config.page_timeout_seconds,
                cancel_event=cancel_event,
            )
            if path.is_file() and path.stat().st_size > 0:
                return path, None
            return None, TaskError(
                "image_download",
                "IMAGE_FILE_MISSING",
                f"图片写入后不存在：{_display_url(url)}",
            )
        except AssetFetchError as error:
            fetch_error = error
        # AVIF/SVG/lazy-loaded and token-protected images often refuse a plain
        # download but render fine in the page; screenshot the element itself
        # so OCR still sees the content.
        fallback = await _capture_image_element(
            page,
            url,
            output_dir,
            evidence_id,
            image_index,
            min(self._config.page_timeout_seconds, 8),
        )
        if fallback is not None:
            return fallback, None
        return None, TaskError(
            "image_download",
            fetch_error.code,
            f"{fetch_error}（{_display_url(url)}）",
        )


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


async def _capture_image_element(
    page: Any,
    url: str,
    output_dir: Path,
    evidence_id: int,
    image_index: int,
    timeout_seconds: int,
) -> Path | None:
    """Screenshot the rendered <img> element when its URL cannot download."""

    if not hasattr(page, "evaluate") or not hasattr(page, "locator"):
        return None
    try:
        marked = await page.evaluate(_MARK_SCRIPT, url)
    except Exception:
        return None
    if not marked:
        return None
    output_path = Path(output_dir).resolve() / (
        f"{evidence_id:03d}_{image_index:02d}_element.png"
    )
    try:
        locator = page.locator("img[data-por-ocr-fallback]").first
        await locator.screenshot(
            path=str(output_path),
            timeout=timeout_seconds * 1000,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        return None
    finally:
        try:
            await page.evaluate(_UNMARK_SCRIPT)
        except Exception:
            pass
    if not output_path.is_file() or output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        return None
    return output_path


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
