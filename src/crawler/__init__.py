"""Browser crawling, platform routing and rendered-page extraction."""

from typing import Any

__all__ = ["ContentParser", "CrawlEngine", "PlatformRouter"]


def __getattr__(name: str) -> Any:
    if name == "ContentParser":
        from src.crawler.content_parser import ContentParser

        return ContentParser
    if name == "CrawlEngine":
        from src.crawler.engine import CrawlEngine

        return CrawlEngine
    if name == "PlatformRouter":
        from src.crawler.platform_router import PlatformRouter

        return PlatformRouter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
