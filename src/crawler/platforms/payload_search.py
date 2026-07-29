"""Bounded search helpers for embedded/network JSON payloads.

Dedicated extractors use these helpers to locate the mapping node that
describes the current content inside large hydration payloads such as
``RENDER_DATA`` (douyin), ``__INITIAL_STATE__`` (xhs) or ``js-initialData``
(zhihu) without depending on a full schema.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def iter_mappings(node: Any, *, max_nodes: int = 8_000) -> Iterable[Mapping[str, Any]]:
    """Yield mapping nodes breadth-first, bounded to avoid pathological payloads."""

    queue: list[Any] = [node]
    seen = 0
    while queue and seen < max_nodes:
        current = queue.pop(0)
        if isinstance(current, Mapping):
            seen += 1
            yield current
            queue.extend(current.values())
        elif isinstance(current, (list, tuple)):
            queue.extend(current)


def find_mapping_with(
    node: Any,
    keys: tuple[str, ...],
    *,
    max_nodes: int = 8_000,
) -> Mapping[str, Any] | None:
    """Return the first mapping (breadth-first) containing every given key."""

    for mapping in iter_mappings(node, max_nodes=max_nodes):
        if all(key in mapping for key in keys):
            return mapping
    return None


def text_at(
    node: Mapping[str, Any],
    keys: Iterable[str],
    *,
    max_chars: int = 100_000,
) -> str | None:
    """Return the first plausible non-empty string among *keys*."""

    for key in keys:
        value = node.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text and len(text) <= max_chars:
                return text
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return None


def url_at(node: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    """Return the first http(s)-looking string among *keys*."""

    for key in keys:
        value = node.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text.startswith(("http://", "https://")):
                return text
            if text.startswith("//"):
                return f"https:{text}"
    return None


def epoch_at(node: Mapping[str, Any], keys: Iterable[str]) -> float | None:
    """Return the first plausible epoch seconds/milliseconds value among *keys*."""

    for key in keys:
        value = node.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if number <= 0:
                continue
            # Millisecond epochs are 13 digits; accept both and normalise.
            if number > 10_000_000_000:
                number = number / 1000.0
            if 946_684_800 <= number <= 4_102_444_800:  # 2000-01-01 .. 2100-01-01
                return number
        elif isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                try:
                    return _epoch_from_int(int(text))
                except ValueError:
                    continue
    return None


def _epoch_from_int(number: int) -> float | None:
    value = float(number)
    if value > 10_000_000_000:
        value = value / 1000.0
    if 946_684_800 <= value <= 4_102_444_800:
        return value
    return None
