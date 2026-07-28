"""Shared types for platform catalog — extracted to avoid circular imports."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Mapping
from urllib.parse import urlsplit


class ExtractorFamily(StrEnum):
    COMMERCE = "commerce"
    ARTICLE = "article"
    SOCIAL = "social"


@dataclass(frozen=True)
class PlatformDefinition:
    key: str
    sheet_name: str
    platform_value: str
    family: ExtractorFamily
    hosts: tuple[str, ...]
    selectors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()

    def matches(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not any(host == item or host.endswith(f".{item}") for item in self.hosts):
            return False
        target = parsed.path.lower()
        if parsed.query:
            target = f"{target}?{parsed.query.lower()}"
        if target in {"", "/"}:
            return False
        if self.include_patterns and not any(re.search(pattern, target) for pattern in self.include_patterns):
            return False
        return not any(re.search(pattern, target) for pattern in self.exclude_patterns)


def _selectors(**values: tuple[str, ...]) -> Mapping[str, tuple[str, ...]]:
    return values
