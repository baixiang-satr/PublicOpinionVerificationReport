"""Orchestrate platform DOM extraction before generic rendered-page fallbacks."""

from __future__ import annotations

from typing import Any

from src.crawler.extractors.catalog import CatalogPlatformExtractor
from src.crawler.extractors.generic import GenericExtractor
from src.crawler.platform_catalog import ExtractorFamily, PlatformDefinition
from src.domain.models import PageData


MERGED_FIELDS = (
    "title",
    "content_text",
    "author_name",
    "author_id",
    "author_url",
    "account_uin",
    "store_name",
    "published_at",
    "published_at_raw",
)


class ContentParser:
    def __init__(self, summary_max_chars: int = 2_000) -> None:
        self._generic = GenericExtractor(summary_max_chars)
        self._platform = CatalogPlatformExtractor()

    async def extract(self, page: Any, definition: PlatformDefinition | None) -> PageData:
        selectors = definition.selectors if definition else None
        document = await self._generic.collect_document(page, selectors)
        fallback = self._generic.extract(document)
        if definition is None:
            return fallback
        primary = self._platform.extract(document, definition)
        merged = self._merge(primary, fallback)
        if definition.family == ExtractorFamily.COMMERCE and not merged.store_name:
            # C2C product pages often expose the seller only as an author/user.
            merged.store_name = merged.author_name
            if "author_name" in merged.field_sources:
                merged.field_sources["store_name"] = merged.field_sources["author_name"]
        self._generic.finalize(merged)
        return merged

    @staticmethod
    def _merge(primary: PageData, fallback: PageData) -> PageData:
        for field in MERGED_FIELDS:
            if not getattr(primary, field):
                setattr(primary, field, getattr(fallback, field))
                if field in fallback.field_sources:
                    primary.field_sources[field] = fallback.field_sources[field]
        primary.image_urls = list(dict.fromkeys([*primary.image_urls, *fallback.image_urls]))
        primary.text_type_hint = fallback.text_type_hint
        return primary
