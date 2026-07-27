"""Generic and template-catalog platform extraction."""

from src.crawler.extractors.base import ImageCandidate, PlatformExtractor, RenderedDocument
from src.crawler.extractors.catalog import CatalogPlatformExtractor
from src.crawler.extractors.generic import GenericExtractor

__all__ = [
    "CatalogPlatformExtractor",
    "GenericExtractor",
    "ImageCandidate",
    "PlatformExtractor",
    "RenderedDocument",
]
