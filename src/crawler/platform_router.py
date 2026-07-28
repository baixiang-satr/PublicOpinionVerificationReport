"""Route final URLs to exact template worksheet and platform enum values."""

from __future__ import annotations

from src.crawler.platform_catalog import (
    ExtractorFamily,
    PlatformDefinition,
    find_platform,
    find_unmapped_template_platform,
)
from src.domain.models import PageData, RouteDecision
from src.domain.template_schema import TEXT_TYPES


class PlatformRouter:
    def definition_for(self, final_url: str) -> PlatformDefinition | None:
        return find_platform(final_url)

    def unsupported_message(self, final_url: str) -> str:
        display_name = find_unmapped_template_platform(final_url)
        if display_name:
            return (
                f"{display_name}不在固定模板的发布平台枚举中；"
                "为避免伪造平台值，未自动路由"
            )
        return "未匹配模板允许的平台"

    def route(self, final_url: str, page: PageData) -> RouteDecision | None:
        definition = self.definition_for(final_url)
        if definition is None:
            return None
        if (
            definition.family == ExtractorFamily.COMMERCE
            and definition.sheet_name == "电商平台"
            and page.text_type_hint != "评论回复"
        ):
            text_type = "商家"
        else:
            text_type = page.text_type_hint if page.text_type_hint in TEXT_TYPES else "正文"
        return RouteDecision(definition.sheet_name, definition.platform_value, text_type)

    @staticmethod
    def is_url_supported_sheet(sheet_name: str) -> bool:
        return sheet_name not in {"群聊", "朋友圈"}
