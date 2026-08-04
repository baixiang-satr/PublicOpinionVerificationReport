"""Map complete runtime records to the exact columns allowed by the fixed template."""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.models import (
    RecordResult,
    RecordStatus,
    TaskError,
    TemplateRow,
)
from src.domain.template_schema import SheetLayout, get_sheet_layout
from src.utils.file_utils import require_safe_file_name
from src.utils.time_utils import as_excel_datetime


# Labels used to keep titles inside 信息内容 on sheets without a title column.
TITLE_HEADING = "【标题】"
BODY_HEADING = "【正文】"


class TemplateRowMappingError(ValueError):
    """Raised when a runtime record cannot truthfully satisfy a template row."""


class TemplateRowMapper:
    """Build rows from auditable records, preserving any unavailable fields as blank."""

    def __init__(self, export_content_max_chars: int = 32_000) -> None:
        self._export_content_max_chars = export_content_max_chars

    def map(self, result: RecordResult) -> TemplateRow:
        if result.route is None:
            raise TemplateRowMappingError("A template route is required for export.")
        if result.status in {
            RecordStatus.PENDING,
            RecordStatus.RUNNING,
            RecordStatus.CANCELLED,
        }:
            raise TemplateRowMappingError(
                f"Record status {result.status.value!r} is not complete enough for export."
            )
        layout = get_sheet_layout(result.route.sheet_name)
        screenshot_name = self._primary_screenshot_name(result, layout)
        attachments = tuple(self._attachment_names(result, layout, screenshot_name))
        fields = self._field_values(result)
        fields["platform"] = result.route.platform_value
        fields["text_type"] = result.route.text_type
        if "title" not in layout.field_columns:
            fields["content"] = _content_with_title(
                result.page.title,
                fields.get("content"),
            )
        self._limit_export_content(result, fields)
        values = self._to_column_values(layout, fields, screenshot_name, attachments)
        self._validate_values(layout, values)
        return TemplateRow(layout.name, result.task.evidence_id, values, screenshot_name, attachments)

    def map_many(self, results: Iterable[RecordResult]) -> list[TemplateRow]:
        return [self.map(result) for result in results]

    @staticmethod
    def _field_values(result: RecordResult) -> dict[str, object | None]:
        page = result.page
        return {
            # Preserve the exact submitted URL so every output row can be
            # reconciled one-to-one with the input, even after redirects.
            "url": result.task.original_url,
            "title": page.title,
            "content": page.content_text or page.content_summary,
            "author_name": page.author_name,
            "author_id": page.author_id,
            "account_uin": page.account_uin,
            "store_name": page.store_name or page.author_name,
            "published_at": as_excel_datetime(page.published_at),
        }

    def _limit_export_content(
        self,
        result: RecordResult,
        fields: dict[str, object | None],
    ) -> None:
        content = str(fields.get("content") or "")
        result.page.original_content_chars = max(
            result.page.original_content_chars,
            len(result.page.content_text or ""),
        )
        if len(content) > self._export_content_max_chars:
            fields["content"] = content[: self._export_content_max_chars]
            if not any(
                error.code == "CONTENT_TRUNCATED_FOR_EXCEL"
                for error in result.errors
            ):
                result.errors.append(
                    TaskError(
                        "export_validation",
                        "CONTENT_TRUNCATED_FOR_EXCEL",
                        (
                            f"信息内容共 {len(content)} 字，Excel 导出保留前 "
                            f"{self._export_content_max_chars} 字"
                        ),
                        retryable=False,
                    )
                )
        result.page.exported_content_chars = len(
            str(fields.get("content") or "")
        )

    @staticmethod
    def _primary_screenshot_name(
        result: RecordResult,
        layout: SheetLayout,
    ) -> str | None:
        """Name written into the primary screenshot column.

        对调表（图文视频/浏览器，``homepage_screenshot_primary``）的主截图列
        是“账号截图”，按合同交付作者个人主页截图；内容页截图改由附件列交付。
        """

        asset = (
            result.assets.author_screenshot
            if layout.homepage_screenshot_primary
            else result.assets.page_screenshot
        )
        if asset is None:
            return None
        return require_safe_file_name(asset.name)

    @staticmethod
    def _attachment_names(
        result: RecordResult,
        layout: SheetLayout,
        primary_screenshot_name: str | None,
    ) -> list[str]:
        names: list[str] = []
        if layout.homepage_screenshot_primary:
            # 对调表：内容页截图取代个人主页截图成为附件列第一槽位。
            page = result.assets.page_screenshot
            candidates = [page, *result.assets.extra_attachments] if page else list(result.assets.extra_attachments)
        else:
            candidates = result.assets.attachment_paths()
        for path in candidates:
            name = require_safe_file_name(path.name)
            if name != primary_screenshot_name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _to_column_values(
        layout: SheetLayout,
        fields: dict[str, object | None],
        screenshot_name: str | None,
        attachments: tuple[str, ...],
    ) -> dict[str, object]:
        values = {
            column: value
            for field, column in layout.field_columns.items()
            if (value := fields.get(field)) is not None and str(value).strip() != ""
        }
        if layout.primary_screenshot_column and screenshot_name:
            values[layout.primary_screenshot_column] = screenshot_name
        if layout.attachment_column and attachments:
            values[layout.attachment_column] = ",".join(attachments)
        return values

    @staticmethod
    def _validate_values(layout: SheetLayout, values: dict[str, object]) -> None:
        for column, allowed_values in layout.validation_values.items():
            value = values.get(column)
            if value is not None and value not in allowed_values:
                raise TemplateRowMappingError(
                    f"{layout.name} column {column} requires one of {allowed_values}, got {value!r}."
                )


def _content_with_title(title: object | None, content: object | None) -> object | None:
    """Preserve a fetched title in fixed sheets that have no title column.

    The fixed template only gives 电商平台 and 公众号 an independent title
    column.  Every other sheet keeps the title inside 信息内容 with explicit
    labels so the extracted title is never lost:

        【标题】……
        【正文】
        ……

    When the title already appears naturally at the start of the body it is
    not duplicated.
    """

    title_text = str(title or "").strip()
    content_text = str(content or "").strip()
    if not title_text:
        return content
    if not content_text:
        return f"{TITLE_HEADING}{title_text}"
    if title_text.casefold() in content_text[: max(240, len(title_text) * 3)].casefold():
        return content
    return f"{TITLE_HEADING}{title_text}\n{BODY_HEADING}\n{content_text}"
