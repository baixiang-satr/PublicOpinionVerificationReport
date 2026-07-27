"""Route final URLs to exact template worksheet and platform enum values."""

from __future__ import annotations

from src.crawler.platform_catalog import PlatformDefinition, find_platform
from src.domain.models import PageData, RouteDecision
from src.domain.template_schema import TEXT_TYPES


class PlatformRouter:
    def definition_for(self, final_url: str) -> PlatformDefinition | None:
        return find_platform(final_url)

    def route(self, final_url: str, page: PageData) -> RouteDecision | None:
        definition = self.definition_for(final_url)
        if definition is None:
            return None
        text_type = page.text_type_hint if page.text_type_hint in TEXT_TYPES else "正文"
        return RouteDecision(definition.sheet_name, definition.platform_value, text_type)

    @staticmethod
    def is_url_supported_sheet(sheet_name: str) -> bool:
        return sheet_name not in {"群聊", "朋友圈"}
