"""Template-aware completeness checks for extracted page fields."""

from __future__ import annotations

from src.domain.models import RecordResult
from src.domain.template_schema import get_sheet_layout


def missing_required_fields(result: RecordResult) -> list[str]:
    if result.route is None or result.assets.page_screenshot is None:
        return ["route_or_screenshot"]
    layout = get_sheet_layout(result.route.sheet_name)
    reverse_fields = {
        column: field
        for field, column in layout.field_columns.items()
    }
    runtime_values = {
        "url": result.page.final_url or result.task.normalized_url,
        "platform": result.route.platform_value,
        "text_type": result.route.text_type,
        "title": result.page.title,
        "content": result.page.content_summary or result.page.content_text,
        "author_name": result.page.author_name,
        "author_id": result.page.author_id,
        "account_uin": result.page.account_uin,
        "store_name": result.page.store_name,
        "published_at": result.page.published_at,
    }
    if "account_uin" in layout.field_columns:
        # 公众号表：微信号(必填)列交付公众号昵称，与导出映射层规则一致。
        runtime_values["author_id"] = result.page.author_name
        runtime_values["account_uin"] = None
    missing: list[str] = []
    for column in sorted(layout.required_columns):
        if column == layout.primary_screenshot_column:
            continue
        field = reverse_fields.get(column)
        if field and not runtime_values.get(field):
            missing.append(field)
    return missing
