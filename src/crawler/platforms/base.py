"""Protocol shared by per-platform dedicated extractors.

A dedicated extractor is consulted before the catalog DOM extraction in
``ContentParser``.  It reads the already-rendered page plus the collected
:class:`RenderedDocument` (embedded JSON, network payloads, DOM values) and
returns high-confidence :class:`PageData`, or ``None`` to let the generic
pipeline continue untouched.
"""
from __future__ import annotations

from typing import Any, Protocol

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.domain.models import PageData


class DedicatedExtractor(Protocol):
    """Platform-specific extraction consulted before catalog DOM extraction."""

    #: PlatformDefinition keys this extractor handles (e.g. ``("douyin",)``).
    platform_keys: tuple[str, ...]

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        """Return platform-verified fields, or ``None`` to fall back.

        Implementations must stay read-only on the live page (no clicking,
        no scrolling beyond what the shared pipeline already did) and must
        never raise: unexpected errors are caught by the caller and treated
        as "no dedicated result".
        """
