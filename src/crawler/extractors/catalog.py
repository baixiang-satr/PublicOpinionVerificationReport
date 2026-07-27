"""Platform-catalog DOM extraction, split by platform family behavior."""

from __future__ import annotations

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_catalog import ExtractorFamily, PlatformDefinition
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_web_published_at


class CatalogPlatformExtractor:
    def extract(self, document: RenderedDocument, definition: PlatformDefinition) -> PageData:
        values = document.platform_values
        data = PageData(final_url=document.url)
        fields = (
            "title",
            "content_text",
            "author_name",
            "author_id",
            "author_url",
            "published_at_raw",
            "store_name",
            "account_uin",
        )
        for field in fields:
            value = values.get(field)
            if value:
                setattr(data, field, value.strip())
                data.field_sources[field] = ExtractionSource.PLATFORM_DOM
        if not data.published_at_raw and values.get("published_at"):
            data.published_at_raw = values["published_at"].strip()
            data.field_sources["published_at_raw"] = ExtractionSource.PLATFORM_DOM
        if data.published_at_raw:
            data.published_at = parse_web_published_at(data.published_at_raw)
        if definition.family == ExtractorFamily.COMMERCE and not data.store_name:
            data.store_name = data.author_name
        return data
