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

        files: list[Path] = []
        errors: list[TaskError] = []
        candidates = list(dict.fromkeys(image_urls))[: self._config.max_images_per_record]
        for url in candidates:
            _raise_if_cancelled(cancel_event)
            try:
                path = await self._fetcher.fetch(
                    page=page,
                    url=url,
                    output_dir=output_dir,
                    evidence_id=evidence_id,
                    image_index=len(files) + 1,
                    max_bytes=self._config.max_image_bytes,
                    timeout_seconds=self._config.page_timeout_seconds,
                    cancel_event=cancel_event,
                )
                if path.is_file() and path.stat().st_size > 0:
                    files.append(path)
                else:
                    errors.append(
                        TaskError(
                            "image_download",
                            "IMAGE_FILE_MISSING",
                            f"图片写入后不存在：{_display_url(url)}",
                        )
                    )
            except AssetFetchError as error:
                errors.append(
                    TaskError(
                        "image_download",
                        error.code,
                        f"{error}（{_display_url(url)}）",
                    )
                )
        return AssetCollectionResult(tuple(files), tuple(errors))


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError
