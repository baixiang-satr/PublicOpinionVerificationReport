"""Orchestrate platform DOM extraction before generic rendered-page fallbacks."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import replace
from typing import Any

from src.crawler.extractors.catalog import CatalogPlatformExtractor
from src.crawler.extractors.generic import GenericExtractor
from src.crawler.field_resolver import merge_page_data
from src.crawler.platform_catalog import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.registry import dedicated_extractor_for
from src.domain.models import ExtractionSource, PageData

logger = logging.getLogger(__name__)


class ContentParser:
    def __init__(self, summary_max_chars: int = 2_000) -> None:
        self._generic = GenericExtractor(summary_max_chars)
        self._platform = CatalogPlatformExtractor()

    async def extract(
        self,
        page: Any,
        definition: PlatformDefinition | None,
        *,
        network_payloads: tuple[Any, ...] = (),
    ) -> PageData:
        selectors = definition.selectors if definition else None
        document = await self._generic.collect_document(page, selectors)
        if network_payloads:
            document = replace(document, network_payloads=network_payloads)
        fallback = self._generic.extract(document)
        if definition is None:
            return fallback
        primary = self._platform.extract(document, definition)
        dedicated = dedicated_extractor_for(definition.key)
        dedicated_data: PageData | None = None
        dedicated_snapshot: PageData | None = None
        if dedicated is not None:
            try:
                dedicated_data = await dedicated.extract(page, document, definition)
            except Exception as error:  # noqa: BLE001 - platform plug-in boundary
                logger.warning(
                    "Dedicated extractor for %s failed: %s", definition.key, error,
                )
                dedicated_data = None
            if dedicated_data is not None:
                # merge_page_data intentionally mutates its primary object;
                # keep an immutable-in-practice snapshot of URL-scoped facts
                # for the post-fallback Douyin authority restore.
                dedicated_snapshot = deepcopy(dedicated_data)
                # Dedicated platform knowledge wins over generic catalog DOM;
                # catalog values still fill fields the extractor did not find.
                primary = self._merge(dedicated_data, primary)
        merged = self._merge(primary, fallback)
        if definition.family == ExtractorFamily.COMMERCE and not merged.store_name:
            # C2C product pages often expose the seller only as an author/user.
            merged.store_name = merged.author_name
            if "author_name" in merged.field_sources:
                merged.field_sources["store_name"] = merged.field_sources["author_name"]
        self._generic.finalize(merged)
        if definition.key == "douyin" and dedicated_snapshot is not None:
            self._finalize_douyin_video(merged, dedicated_snapshot)
        if definition.key == "xiaohongshu" and dedicated_snapshot is not None:
            self._finalize_xiaohongshu_note(merged, dedicated_snapshot)
        return merged

    @staticmethod
    def _merge(primary: PageData, fallback: PageData) -> PageData:
        return merge_page_data(primary, fallback)

    def _finalize_douyin_video(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep target-aweme facts authoritative over page-shell fallbacks.

        The generic collector intentionally scans all hydration/network JSON.
        Douyin pages also ship configuration, live-room and recommendation
        objects which are useful on other routes but are not evidence for the
        requested video.  Once the dedicated extractor matched the URL's
        aweme id, restore its visible publish time after generic finalization
        and keep recommendation thumbnails out of the OCR queue.
        """

        for field in (
            "title",
            "content_text",
            "author_name",
            "author_id",
            "author_url",
        ):
            value = getattr(dedicated, field)
            if value is None:
                continue
            setattr(merged, field, value)
            source = dedicated.field_sources.get(field)
            if source is not None:
                merged.field_sources[field] = source
                merged.field_confidences[field] = (
                    dedicated.field_confidences.get(field, 0.9)
                )
        # Re-clean the restored caption and recompute summary/length metrics.
        # Generic finalization may already have summarized the unrelated
        # configuration node before this authoritative restore.
        self._generic.finalize(merged)
        if dedicated.published_at is not None:
            merged.published_at = dedicated.published_at
            merged.published_at_raw = dedicated.published_at_raw
            published_source = dedicated.field_sources.get("published_at")
            raw_source = dedicated.field_sources.get("published_at_raw")
            if published_source is not None:
                merged.field_sources["published_at"] = published_source
            if raw_source is not None:
                merged.field_sources["published_at_raw"] = raw_source
            merged.field_confidences["published_at"] = (
                dedicated.field_confidences.get("published_at", 0.86)
            )
            if dedicated.published_at_raw is not None:
                merged.field_confidences["published_at_raw"] = (
                    dedicated.field_confidences.get("published_at_raw", 0.86)
                )
        if "/video/" in (merged.final_url or ""):
            # Video-page thumbnails belong to recommendations/player chrome,
            # not body images.  OCRing them appended unrelated text to 信息内容.
            merged.image_urls = []

    def _finalize_xiaohongshu_note(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep the URL-matched note above recommendations and comments.

        Xiaohongshu detail pages ship recommendation cards and comment users
        beside the target note.  The hexadecimal note ID is not covered by
        the generic numeric URL scoping rule, so a higher-confidence network
        candidate could otherwise replace the requested note after the
        dedicated extractor selected it.
        """

        authoritative_sources = {
            source
            for field in ("title", "content_text")
            if (source := dedicated.field_sources.get(field)) is not None
        }
        if not authoritative_sources.intersection(
            {
                ExtractionSource.EMBEDDED_JSON,
                ExtractionSource.NETWORK_JSON,
            }
        ):
            return

        for field in (
            "title",
            "content_text",
            "author_name",
            "author_id",
            "author_url",
        ):
            value = getattr(dedicated, field)
            if value is None:
                continue
            setattr(merged, field, value)
            source = dedicated.field_sources.get(field)
            if source is not None:
                merged.field_sources[field] = source
                merged.field_confidences[field] = (
                    dedicated.field_confidences.get(field, 0.94)
                )

        # Only images belonging to the selected note may enter the temporary
        # OCR queue; carousel recommendations and avatars are unrelated.
        merged.image_urls = list(dedicated.image_urls)
        self._generic.finalize(merged)

        if dedicated.published_at is not None:
            merged.published_at = dedicated.published_at
            merged.published_at_raw = dedicated.published_at_raw
            source = dedicated.field_sources.get("published_at")
            if source is not None:
                merged.field_sources["published_at"] = source
                merged.field_confidences["published_at"] = (
                    dedicated.field_confidences.get("published_at", 0.94)
                )
            if dedicated.published_at_raw is not None:
                raw_source = dedicated.field_sources.get("published_at_raw")
                if raw_source is not None:
                    merged.field_sources["published_at_raw"] = raw_source
                    merged.field_confidences["published_at_raw"] = (
                        dedicated.field_confidences.get(
                            "published_at_raw",
                            0.94,
                        )
                    )
