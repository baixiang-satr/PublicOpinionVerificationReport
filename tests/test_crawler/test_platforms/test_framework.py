from typing import Any

import pytest

from src.crawler.extractors.base import RenderedDocument
from src.crawler.platform_types import PlatformDefinition
from src.crawler.platforms.payload_search import (
    epoch_at,
    find_mapping_with,
    iter_mappings,
    text_at,
    url_at,
)
from src.crawler.platforms.registry import dedicated_extractor_for, register
from src.domain.models import PageData


class _DummyExtractor:
    platform_keys = ("dummy_platform",)

    async def extract(
        self,
        page: Any,
        document: RenderedDocument,
        definition: PlatformDefinition,
    ) -> PageData | None:
        return PageData(title="dummy")


class _FailingExtractor:
    platform_keys = ("failing_platform",)

    async def extract(self, page: Any, document: Any, definition: Any) -> None:
        raise RuntimeError("boom")


def test_registry_register_and_lookup() -> None:
    extractor = _DummyExtractor()
    register(extractor)
    try:
        assert dedicated_extractor_for("dummy_platform") is extractor
        assert dedicated_extractor_for("unknown_platform") is None
    finally:
        from src.crawler.platforms import registry

        registry._REGISTRY.pop("dummy_platform", None)


def test_registry_rejects_duplicate_keys() -> None:
    first = _DummyExtractor()
    register(first)
    try:
        with pytest.raises(ValueError, match="Duplicate"):
            register(_DummyExtractor())
    finally:
        from src.crawler.platforms import registry

        registry._REGISTRY.pop("dummy_platform", None)


def test_payload_search_iter_mappings_is_bounded() -> None:
    payload = {"a": {"b": {"c": 1}}, "list": [{"d": 2}, [3, {"e": 4}]]}
    mappings = list(iter_mappings(payload))
    assert any("c" in mapping for mapping in mappings)
    assert any("d" in mapping for mapping in mappings)
    assert any("e" in mapping for mapping in mappings)


def test_payload_search_find_mapping_with() -> None:
    payload = {
        "root": {
            "children": [
                {"unrelated": True},
                {"title": "视频标题", "desc": "视频简介"},
            ]
        }
    }
    found = find_mapping_with(payload, ("title", "desc"))
    assert found is not None
    assert found["title"] == "视频标题"


def test_payload_search_text_url_epoch_helpers() -> None:
    node = {
        "name": "  昵称  ",
        "count": 42,
        "home": "//example.test/u/1",
        "created_ms": 1_751_000_000_000,
        "created_s": "1751000000",
        "bad_time": 5,
    }
    assert text_at(node, ("missing", "name")) == "昵称"
    assert text_at(node, ("count",)) == "42"
    assert url_at(node, ("home",)) == "https://example.test/u/1"
    assert epoch_at(node, ("created_ms",)) == 1_751_000_000.0
    assert epoch_at(node, ("created_s",)) == 1_751_000_000.0
    assert epoch_at(node, ("bad_time",)) is None
