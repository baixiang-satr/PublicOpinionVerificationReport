"""Orchestrate platform DOM extraction before generic rendered-page fallbacks."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import replace
from typing import Any

from src.crawler.extractors.catalog import CatalogPlatformExtractor
from src.crawler.extractors.generic import GenericExtractor
from src.crawler.dedicated_restore import (
    restore_dedicated_fields,
    restore_dedicated_time,
)
from src.crawler.field_resolver import merge_page_data
from src.crawler.api_assist import douyin_aweme_id
from src.crawler.platform_catalog import ExtractorFamily, PlatformDefinition
from src.crawler.platforms.bilibili import bilibili_video_id
from src.crawler.platforms.registry import dedicated_extractor_for
from src.crawler.platforms.baijiahao import is_video_landing_url
from src.crawler.platforms.kuaishou import kuaishou_photo_id
from src.crawler.platforms.tieba import sanitize_tieba_content
from src.domain.models import ExtractionSource, PageData
from src.utils.time_utils import parse_web_published_at

logger = logging.getLogger(__name__)

#: 专用提取器未命中时不再可信的载荷来源（配置/推荐节点混居其中）。
_UNTRUSTED_PAYLOAD_SOURCES = frozenset(
    {ExtractionSource.NETWORK_JSON, ExtractionSource.EMBEDDED_JSON}
)

#: 未命中守卫会剥离的字段。
_PAYLOAD_FIELD_NAMES = (
    "title", "content_text", "author_name", "author_id",
    "author_url", "published_at", "published_at_raw",
)


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
        dedicated_data: PageData | None = None
        dedicated_snapshot: PageData | None = None
        if dedicated is not None:
            try:
                dedicated_data = await dedicated.extract(page, document, definition)
            except Exception as error:  # noqa: BLE001 - platform plug-in boundary
                logger.warning(
                    "Dedicated extractor for %s failed: %s", definition.key, error,
                )
                dedicated_data = None
            if dedicated_data is not None:
                # merge_page_data intentionally mutates its primary object;
                # keep an immutable-in-practice snapshot of URL-scoped facts
                # for the post-fallback Douyin authority restore.
                dedicated_snapshot = deepcopy(dedicated_data)
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
        if definition.key == "douyin" and dedicated_snapshot is not None:
            self._finalize_douyin_video(merged, dedicated_snapshot)
        if definition.key == "douyin" and dedicated_snapshot is None:
            self._strip_untrusted_payload_fields(
                merged, document, douyin_aweme_id(document.url), strip_images=True
            )
        if definition.key == "bilibili" and dedicated_snapshot is None:
            self._strip_untrusted_payload_fields(
                merged, document, bilibili_video_id(document.url)
            )
        if definition.key == "xiaohongshu" and dedicated_snapshot is not None:
            self._finalize_xiaohongshu_note(merged, dedicated_snapshot)
        if definition.key == "kuaishou" and dedicated_snapshot is not None:
            self._finalize_kuaishou_video(merged, dedicated_snapshot)
        if definition.key == "kuaishou" and dedicated_snapshot is None:
            # 与抖音/B站同规：目标 photo 未命中时推荐/配置节点不是证据，
            # 剥离后回 DOM 兜底，宁可留空待补录。
            self._strip_untrusted_payload_fields(
                merged, document, kuaishou_photo_id(document.url), strip_images=True
            )
        if definition.key == "ixigua" and dedicated_snapshot is not None:
            self._finalize_ixigua_video(merged)
        if definition.key == "netease_news" and dedicated_snapshot is not None:
            self._finalize_netease_article(merged, dedicated_snapshot)
        if definition.key == "sohu_video" and dedicated_snapshot is not None:
            self._finalize_sohu_video(merged, dedicated_snapshot)
        if definition.key == "baijiahao" and is_video_landing_url(document.url):
            self._finalize_baijiahao_video(merged, dedicated_snapshot)
        if definition.key == "tieba":
            self._finalize_tieba_post(merged)
        return merged

    @staticmethod
    def _merge(primary: PageData, fallback: PageData) -> PageData:
        return merge_page_data(primary, fallback)

    def _finalize_douyin_video(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep target-aweme facts authoritative over page-shell fallbacks.

        The generic collector intentionally scans all hydration/network JSON.
        Douyin pages also ship configuration, live-room and recommendation
        objects which are useful on other routes but are not evidence for the
        requested video.  Once the dedicated extractor matched the URL's
        aweme id, restore its visible publish time after generic finalization
        and keep recommendation thumbnails out of the OCR queue.
        """

        for field in (
            "title",
            "content_text",
            "author_name",
            "author_id",
            "author_url",
        ):
            value = getattr(dedicated, field)
            if value is None:
                continue
            setattr(merged, field, value)
            source = dedicated.field_sources.get(field)
            if source is not None:
                merged.field_sources[field] = source
                merged.field_confidences[field] = (
                    dedicated.field_confidences.get(field, 0.9)
                )
        # Re-clean the restored caption and recompute summary/length metrics.
        # Generic finalization may already have summarized the unrelated
        # configuration node before this authoritative restore.
        self._generic.finalize(merged)
        if dedicated.published_at is not None:
            merged.published_at = dedicated.published_at
            merged.published_at_raw = dedicated.published_at_raw
            published_source = dedicated.field_sources.get("published_at")
            raw_source = dedicated.field_sources.get("published_at_raw")
            if published_source is not None:
                merged.field_sources["published_at"] = published_source
            if raw_source is not None:
                merged.field_sources["published_at_raw"] = raw_source
            merged.field_confidences["published_at"] = (
                dedicated.field_confidences.get("published_at", 0.86)
            )
            if dedicated.published_at_raw is not None:
                merged.field_confidences["published_at_raw"] = (
                    dedicated.field_confidences.get("published_at_raw", 0.86)
                )
        else:
            # 专用节点已锁定目标但未产出时间时，通用侧来自网络载荷的裸数字
            # （如直播回放异常的 245000）不是可展示时间，宁可留空待补录。
            _drop_implausible_published_at(merged)
        if "/video/" in (merged.final_url or ""):
            # Video-page thumbnails belong to recommendations/player chrome,
            # not body images.  OCRing them appended unrelated text to 信息内容.
            merged.image_urls = []

    def _strip_untrusted_payload_fields(
        self,
        merged: PageData,
        document: Any,
        url_content_id: str | None,
        *,
        strip_images: bool = False,
    ) -> None:
        """专用提取器未命中但 URL 携带内容 id：剥离载荷来源字段，回到 DOM 兜底。

        抖音/B站页面同时携带配置、直播与推荐节点（「厂牌排名规则」教训）；
        专用提取器按 URL id 匹配失败时，通用侧从网络/内嵌载荷抓到的字段
        不是目标内容的证据。剥离后用页面可见文本/DOM 标题回填，仍为空则
        留空待补录——宁可留空，不给错误内容。
        """

        if not url_content_id:
            return
        stripped = False
        for field in _PAYLOAD_FIELD_NAMES:
            if merged.field_sources.get(field) not in _UNTRUSTED_PAYLOAD_SOURCES:
                continue
            if getattr(merged, field) is not None:
                setattr(merged, field, None)
                stripped = True
            merged.field_sources.pop(field, None)
            merged.field_confidences.pop(field, None)
        _drop_implausible_published_at(merged)
        if strip_images:
            merged.image_urls = []
        if not stripped:
            return
        if merged.content_text is None:
            dom_content = document.dom_values.get("content_text") or document.visible_text
            if dom_content:
                merged.content_text = dom_content
                merged.field_sources["content_text"] = ExtractionSource.VISIBLE_TEXT
                merged.field_confidences["content_text"] = 0.5
        if merged.title is None and document.title:
            merged.title = document.title
            merged.field_sources["title"] = ExtractionSource.GENERIC_DOM
            merged.field_confidences["title"] = 0.5
        self._generic.finalize(merged)  # 重算摘要/长度/内容类型

    def _finalize_tieba_post(self, merged: PageData) -> None:
        """贴吧正文守卫：兜底来源的整页文本一律净化，不可信则留空。

        专用探针失败时通用侧会把整页可见文本（导航/吧列表/版权尾）填进
        正文；宁可留空待补录，也不交付与真实内容不符的文本。
        """

        if not merged.content_text:
            return
        if merged.field_sources.get("content_text") is ExtractionSource.EMBEDDED_JSON:
            return  # 专用提取器内部已净化
        cleaned = sanitize_tieba_content(merged.content_text, merged.title)
        if cleaned == merged.content_text.strip():
            return
        merged.content_text = cleaned
        if cleaned is None:
            merged.field_sources.pop("content_text", None)
            merged.field_confidences.pop("content_text", None)
        self._generic.finalize(merged)

    def _finalize_xiaohongshu_note(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep the URL-matched note above recommendations and comments.

        Xiaohongshu detail pages ship recommendation cards and comment users
        beside the target note.  The hexadecimal note ID is not covered by
        the generic numeric URL scoping rule, so a higher-confidence network
        candidate could otherwise replace the requested note after the
        dedicated extractor selected it.
        """

        authoritative_sources = {
            source
            for field in ("title", "content_text")
            if (source := dedicated.field_sources.get(field)) is not None
        }
        if not authoritative_sources.intersection(
            {
                ExtractionSource.EMBEDDED_JSON,
                ExtractionSource.NETWORK_JSON,
            }
        ):
            return

        for field in (
            "title",
            "content_text",
            "author_name",
            "author_id",
            "author_url",
        ):
            value = getattr(dedicated, field)
            if value is None:
                continue
            setattr(merged, field, value)
            source = dedicated.field_sources.get(field)
            if source is not None:
                merged.field_sources[field] = source
                merged.field_confidences[field] = (
                    dedicated.field_confidences.get(field, 0.94)
                )

        # Only images belonging to the selected note may enter the temporary
        # OCR queue; carousel recommendations and avatars are unrelated.
        merged.image_urls = list(dedicated.image_urls)
        self._generic.finalize(merged)

        if dedicated.published_at is not None:
            merged.published_at = dedicated.published_at
            merged.published_at_raw = dedicated.published_at_raw
            source = dedicated.field_sources.get("published_at")
            if source is not None:
                merged.field_sources["published_at"] = source
                merged.field_confidences["published_at"] = (
                    dedicated.field_confidences.get("published_at", 0.94)
                )
            if dedicated.published_at_raw is not None:
                raw_source = dedicated.field_sources.get("published_at_raw")
                if raw_source is not None:
                    merged.field_sources["published_at_raw"] = raw_source
                    merged.field_confidences["published_at_raw"] = (
                        dedicated.field_confidences.get(
                            "published_at_raw",
                            0.94,
                        )
                    )

    def _finalize_kuaishou_video(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep the URL-matched Kuaishou photo above recommendation feeds."""

        authoritative_sources = {
            source
            for field in ("title", "content_text")
            if (source := dedicated.field_sources.get(field)) is not None
        }
        if not authoritative_sources.intersection(
            {
                ExtractionSource.EMBEDDED_JSON,
                ExtractionSource.NETWORK_JSON,
            }
        ):
            return

        for field in (
            "title",
            "content_text",
            "author_name",
            "author_id",
            "author_url",
        ):
            value = getattr(dedicated, field)
            if value is None:
                continue
            setattr(merged, field, value)
            source = dedicated.field_sources.get(field)
            if source is not None:
                merged.field_sources[field] = source
                merged.field_confidences[field] = (
                    dedicated.field_confidences.get(field, 0.94)
                )

        # Video covers, avatars and recommendation cards are page chrome, not
        # body images.  Excluding them prevents OCR from appending unrelated
        # recommendation text to the requested caption.
        merged.image_urls = []
        self._generic.finalize(merged)

        if dedicated.published_at is not None:
            merged.published_at = dedicated.published_at
            merged.published_at_raw = dedicated.published_at_raw
            source = dedicated.field_sources.get("published_at")
            if source is not None:
                merged.field_sources["published_at"] = source
                merged.field_confidences["published_at"] = (
                    dedicated.field_confidences.get("published_at", 0.94)
                )
            if dedicated.published_at_raw is not None:
                raw_source = dedicated.field_sources.get("published_at_raw")
                if raw_source is not None:
                    merged.field_sources["published_at_raw"] = raw_source
                    merged.field_confidences["published_at_raw"] = (
                        dedicated.field_confidences.get(
                            "published_at_raw",
                            0.94,
                        )
                    )

    def _finalize_ixigua_video(self, merged: PageData) -> None:
        """Clean ixigua share-page values: title suffix and boilerplate desc.

        The mobile share page's JSON-LD ships ``<title> | 西瓜视频`` and a
        boilerplate description (「…,于…上线。西瓜视频为您提供…」)。标题以
        页面 H1 为准；无语义简介时正文即标题（同抖音文案约定）。
        """

        suffix = " | 西瓜视频"
        title = (merged.title or "").strip()
        if title.endswith(suffix):
            merged.title = title[: -len(suffix)].strip()
        content = (merged.content_text or "").strip()
        if content and "西瓜视频为您提供" in content:
            cleaned = content.split("西瓜视频为您提供", 1)[0]
            # 「标题,于2026年7月29日上线。」形态再去掉上线尾巴
            cleaned = cleaned.split(",于20", 1)[0].strip().rstrip(",，。 ")
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
            merged.content_text = cleaned or merged.title

    def _finalize_netease_article(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep the article body/source above comments and platform metadata."""

        restore_dedicated_fields(merged, dedicated)
        # Only images beneath the selected article body may enter OCR.  The
        # generic page collector also sees recommendation cards and avatars.
        merged.image_urls = list(dedicated.image_urls)
        self._generic.finalize(merged)
        restore_dedicated_time(merged, dedicated)

    def _finalize_sohu_video(
        self,
        merged: PageData,
        dedicated: PageData,
    ) -> None:
        """Keep ID-matched player globals above navigation/player chrome."""

        restore_dedicated_fields(merged, dedicated)
        # Player covers and related cards are not body images or transcripts.
        merged.image_urls = []
        self._generic.finalize(merged)
        restore_dedicated_time(merged, dedicated)

    def _finalize_baijiahao_video(
        self,
        merged: PageData,
        dedicated: PageData | None,
    ) -> None:
        """Never merge Baidu download chrome into an ``nid`` video record."""

        protected_fields = (
            "title",
            "content_text",
            "content_summary",
            "author_name",
            "author_id",
            "author_url",
            "published_at",
            "published_at_raw",
        )
        for field in protected_fields:
            setattr(merged, field, None)
            merged.field_sources.pop(field, None)
            merged.field_confidences.pop(field, None)
        merged.image_urls = []
        merged.summary_truncated = False
        merged.original_content_chars = 0
        merged.exported_content_chars = 0
        merged.author_id_is_fallback = False
        if dedicated is None:
            merged.field_rejection_notes.append(
                "MBD video nid was not matched by a target-scoped video payload; "
                "page chrome and recommendations were discarded."
            )
            return
        restore_dedicated_fields(merged, dedicated)
        merged.image_urls = list(dedicated.image_urls)
        self._generic.finalize(merged)
        restore_dedicated_time(merged, dedicated)


def _drop_implausible_published_at(data: PageData) -> None:
    """裸数字且不可解析的「时间」（如直播回放异常的 245000）宁可留空。"""

    raw = (data.published_at_raw or "").strip()
    implausible = data.published_at is None or data.published_at.year < 2000
    if raw.isdigit() and parse_web_published_at(raw) is None and implausible:
        data.published_at_raw = None
        data.published_at = None
        data.field_sources.pop("published_at_raw", None)
        data.field_sources.pop("published_at", None)
        data.field_confidences.pop("published_at_raw", None)
        data.field_confidences.pop("published_at", None)
