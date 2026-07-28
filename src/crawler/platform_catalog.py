"""Central URL routing and DOM selector catalog for template-supported platforms."""
from __future__ import annotations
from urllib.parse import urlsplit

from src.domain.template_schema import SHEET_LAYOUTS
from src.crawler.platform_types import ExtractorFamily, PlatformDefinition, _selectors

# Data definitions are in platform_data.py to keep files under the line limit.
# Import and re-export for backward compatibility with existing import paths.
from src.crawler.platform_data import (  # noqa: E402  # isort:skip
    UNMAPPED_TEMPLATE_HOSTS,
    PLATFORM_DEFINITIONS,
)


def find_platform(url: str) -> PlatformDefinition | None:
    parsed = urlsplit(url)
    if (
        (parsed.hostname or "").lower() == "h5-ol.sns.sohu.com"
        and parsed.path.lower().startswith("/hy-super-h5/share/")
    ):
        return next((definition for definition in PLATFORM_DEFINITIONS if definition.key == "huyou"), None)
    return next((definition for definition in PLATFORM_DEFINITIONS if definition.matches(url)), None)


def find_unmapped_template_platform(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return next(
        (
            display_name
            for domain, display_name in UNMAPPED_TEMPLATE_HOSTS.items()
            if host == domain or host.endswith(f".{domain}")
        ),
        None,
    )


def validate_catalog() -> None:
    keys: set[str] = set()
    for definition in PLATFORM_DEFINITIONS:
        if definition.key in keys:
            raise ValueError(f"Duplicate platform key: {definition.key}")
        keys.add(definition.key)
        layout = SHEET_LAYOUTS[definition.sheet_name]
        platform_column = layout.field_columns["platform"]
        allowed = layout.validation_values[platform_column]
        if definition.platform_value not in allowed:
            raise ValueError(f"Platform value is outside template contract: {definition.platform_value}")


validate_catalog()
