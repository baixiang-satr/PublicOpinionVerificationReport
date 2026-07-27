"""Finalize author facts and record auditable nickname-to-ID fallbacks."""

from __future__ import annotations

from src.domain.models import ExtractionSource, PageData, RouteDecision


ID_REQUIRED_SHEETS = frozenset({"公众号", "图文视频", "浏览器"})


class AuthorExtractor:
    def __init__(self, allow_nickname_as_id: bool = True) -> None:
        self._allow_nickname_as_id = allow_nickname_as_id

    def finalize(self, page: PageData, route: RouteDecision) -> None:
        if route.sheet_name == "电商平台" and not page.store_name:
            page.store_name = page.author_name
        if (
            route.sheet_name in ID_REQUIRED_SHEETS
            and not page.author_id
            and page.author_name
            and self._allow_nickname_as_id
        ):
            page.author_id = page.author_name
            page.author_id_is_fallback = True
            page.field_sources["author_id"] = ExtractionSource.NICKNAME_FALLBACK
