"""Registry mapping platform keys to dedicated extractors.

Platform modules register themselves at import time; ``register_defaults``
imports the built-in platform modules once so ``ContentParser`` can stay
agnostic of the concrete extractor classes.
"""
from __future__ import annotations

import importlib
import logging

from src.crawler.platforms.base import DedicatedExtractor

logger = logging.getLogger(__name__)

# Built-in extractor modules, imported lazily on first use.  Each module must
# call ``register(...)`` for every extractor it defines.
_DEFAULT_MODULES: tuple[str, ...] = (
    "src.crawler.platforms.douyin",
    "src.crawler.platforms.kuaishou",
    "src.crawler.platforms.xiaohongshu",
    "src.crawler.platforms.weibo",
    "src.crawler.platforms.zhihu",
    "src.crawler.platforms.tieba",
    "src.crawler.platforms.baijiahao",
    "src.crawler.platforms.wechat",
    "src.crawler.platforms.bytedance_ssr",
    "src.crawler.platforms.netease_news",
    "src.crawler.platforms.sohu_video",
    "src.crawler.platforms.bilibili",
)

_REGISTRY: dict[str, DedicatedExtractor] = {}
_defaults_loaded = False


def register(extractor: DedicatedExtractor) -> DedicatedExtractor:
    for key in extractor.platform_keys:
        if key in _REGISTRY and _REGISTRY[key] is not extractor:
            raise ValueError(f"Duplicate dedicated extractor for platform: {key}")
        _REGISTRY[key] = extractor
    return extractor


def dedicated_extractor_for(platform_key: str) -> DedicatedExtractor | None:
    _ensure_defaults()
    return _REGISTRY.get(platform_key)


def _ensure_defaults() -> None:
    global _defaults_loaded
    if _defaults_loaded:
        return
    _defaults_loaded = True
    for module_name in _DEFAULT_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("Dedicated extractor module %s failed to load: %s", module_name, error)
