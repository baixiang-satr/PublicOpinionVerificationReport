"""Orchestrate platform DOM extraction before generic rendered-page fallbacks."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from src.crawler.extractors.catalog import CatalogPlatformExtractor
from src.crawler.extractors.generic import GenericExtractor
from src.crawler.field_resolver import merge_page_data
from src.crawler.platform_catalog import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.registry import dedicated_extractor_for
from src.domain.models import PageData

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
        if dedicated is not None:
            try:
                dedicated_data = await dedicated.extract(page, document, definition)
            except Exception as error:
                logger.warning(
                    "Dedicated extractor for %s failed: %s", definition.key, error,
                )
                dedicated_data = None
            if dedicated_data is not None:
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
        return merged

    @staticmethod
    def _merge(primary: PageData, fallback: PageData) -> PageData:
        return merge_page_data(primary, fallback)
