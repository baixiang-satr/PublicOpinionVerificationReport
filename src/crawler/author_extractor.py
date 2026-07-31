"""Finalize author facts and record auditable nickname-to-ID fallbacks."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from src.crawler.author_profile_urls import derive_author_profile_url
from src.domain.models import ExtractionSource, PageData, RouteDecision


class AuthorExtractor:
    def __init__(self, allow_nickname_as_id: bool = True) -> None:
        self._allow_nickname_as_id = allow_nickname_as_id

    def finalize(self, page: PageData, route: RouteDecision) -> None:
        if page.author_url and not urlsplit(page.author_url).scheme and page.final_url:
            page.author_url = urljoin(page.final_url, page.author_url)
        if not page.author_url and not page.author_id_is_fallback:
            page.author_url = derive_author_profile_url(page.final_url, page.author_id)
            if page.author_url:
                page.field_sources["author_url"] = ExtractionSource.DERIVED_URL
        if route.sheet_name == "电商平台" and not page.store_name:
            page.store_name = page.author_name
        # Every crawled website follows the same auditable fallback rule.
        # Some sheets (notably 生活资讯 / 今日头条) keep 用户账号 optional,
        # but an available nickname is still more useful than a blank cell.
        # A later identity-verified profile extraction may replace this value
        # with the real public account ID.
        if not page.author_id and page.author_name and self._allow_nickname_as_id:
            page.author_id = page.author_name
            page.author_id_is_fallback = True
            page.field_sources["author_id"] = ExtractionSource.NICKNAME_FALLBACK
            page.field_confidences["author_id"] = 0.25
