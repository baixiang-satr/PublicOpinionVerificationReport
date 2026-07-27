"""Map complete runtime records to the exact columns allowed by the fixed template."""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.models import RecordResult, RecordStatus, TemplateRow
from src.domain.template_schema import SheetLayout, get_sheet_layout
from src.utils.file_utils import require_safe_file_name
from src.utils.time_utils import as_excel_datetime


class TemplateRowMappingError(ValueError):
    """Raised when a runtime record cannot truthfully satisfy a template row."""


class TemplateRowMapper:
    """Build rows only from records explicitly marked ready for fixed-template export."""

    def map(self, result: RecordResult) -> TemplateRow:
        if result.status != RecordStatus.READY_FOR_EXPORT or result.route is None:
            raise TemplateRowMappingError("Only READY_FOR_EXPORT records with a route can be exported.")
        layout = get_sheet_layout(result.route.sheet_name)
        screenshot_name = self._page_screenshot_name(result)
        attachments = tuple(self._attachment_names(result, screenshot_name))
        fields = self._field_values(result)
        fields["platform"] = result.route.platform_value
        fields["text_type"] = result.route.text_type
        values = self._to_column_values(layout, fields, screenshot_name, attachments)
        self._validate_values(layout, values)
        return TemplateRow(layout.name, result.task.evidence_id, values, screenshot_name, attachments)

    def map_many(self, results: Iterable[RecordResult]) -> list[TemplateRow]:
        return [self.map(result) for result in results]

    @staticmethod
    def _field_values(result: RecordResult) -> dict[str, object | None]:
        page = result.page
        return {
            "url": page.final_url or result.task.normalized_url,
            "title": page.title,
            "content": page.content_summary or page.content_text,
            "author_name": page.author_name,
            "author_id": page.author_id,
            "account_uin": page.account_uin,
            "store_name": page.store_name or page.author_name,
            "published_at": as_excel_datetime(page.published_at),
        }

    @staticmethod
    def _page_screenshot_name(result: RecordResult) -> str:
        if result.assets.page_screenshot is None:
            raise TemplateRowMappingError("A primary page screenshot is required for export.")
        return require_safe_file_name(result.assets.page_screenshot.name)

    @staticmethod
    def _attachment_names(result: RecordResult, page_screenshot_name: str) -> list[str]:
        names: list[str] = []
        for path in result.assets.attachment_paths():
            name = require_safe_file_name(path.name)
            if name != page_screenshot_name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _to_column_values(
        layout: SheetLayout,
        fields: dict[str, object | None],
        screenshot_name: str,
        attachments: tuple[str, ...],
    ) -> dict[str, object]:
        values = {
            column: value
            for field, column in layout.field_columns.items()
            if (value := fields.get(field)) is not None and str(value).strip() != ""
        }
        if layout.primary_screenshot_column:
            values[layout.primary_screenshot_column] = screenshot_name
        if layout.attachment_column and attachments:
            values[layout.attachment_column] = ",".join(attachments)
        return values

    @staticmethod
    def _validate_values(layout: SheetLayout, values: dict[str, object]) -> None:
        missing = sorted(column for column in layout.required_columns if not values.get(column))
        if missing:
            raise TemplateRowMappingError(f"{layout.name} is missing required columns: {', '.join(missing)}")
        for column, allowed_values in layout.validation_values.items():
            value = values.get(column)
            if value is not None and value not in allowed_values:
                raise TemplateRowMappingError(
                    f"{layout.name} column {column} requires one of {allowed_values}, got {value!r}."
                )
