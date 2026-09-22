"""Platform-catalog DOM extraction, split by platform family behavior."""

from __future__ import annotations

from src.crawler.extractors.base import RenderedDocument
from src.crawler.field_resolver import consider_field
from src.crawler.platform_catalog import ExtractorFamily, PlatformDefinition
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_published_at_from_text, select_latest_published_at


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
                consider_field(
                    data,
                    field,
                    value,
                    ExtractionSource.PLATFORM_DOM,
                )
        # 页面多时间候选（主帖/评论/推荐流）取距离现在最近的主帖区候选，
        # raw 同步为选中候选原文；无候选时保留首个非空匹配的历史行为。
        published_raw = values.get("published_at")
        selected = select_latest_published_at(document.published_at_candidates)
        if selected is not None and selected[1]:
            published_raw = str(selected[1])
        if not data.published_at_raw and published_raw:
            consider_field(
                data,
                "published_at_raw",
                published_raw,
                ExtractionSource.PLATFORM_DOM,
            )
        if data.published_at_raw:
            data.published_at = parse_published_at_from_text(data.published_at_raw)
            if data.published_at is not None:
                data.field_sources["published_at"] = ExtractionSource.PLATFORM_DOM
                data.field_confidences["published_at"] = (
                    data.field_confidences.get("published_at_raw", 0.86)
                )
        if definition.family == ExtractorFamily.COMMERCE and not data.store_name:
            data.store_name = data.author_name
        return data
