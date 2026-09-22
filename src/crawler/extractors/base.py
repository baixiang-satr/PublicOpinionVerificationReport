"""Typed rendered-document models and the platform extractor protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.crawler.platform_catalog import PlatformDefinition
from src.domain.models import PageData


@dataclass(frozen=True)
class ImageCandidate:
    url: str
    width: int = 0
    height: int = 0
    alt: str = ""
    context: str = ""
    in_content: bool = False


@dataclass(frozen=True)
class RenderedDocument:
    url: str
    title: str = ""
    visible_text: str = ""
    canonical_url: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    json_ld: tuple[str, ...] = ()
    embedded_payloads: tuple[Any, ...] = ()
    network_payloads: tuple[Any, ...] = ()
    dom_values: dict[str, str] = field(default_factory=dict)
    platform_values: dict[str, str] = field(default_factory=dict)
    # 页面全部发布时间候选（text, 是否评论区/推荐区），供「取最新」仲裁
    published_at_candidates: tuple[tuple[str, bool], ...] = ()
    images: tuple[ImageCandidate, ...] = ()


class PlatformExtractor(Protocol):
    def extract(self, document: RenderedDocument, definition: PlatformDefinition) -> PageData: ...
