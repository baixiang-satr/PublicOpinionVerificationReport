"""Storage-state merge helpers for clean browser shutdown."""

from __future__ import annotations

from typing import Any


def preserve_indexed_db(
    previous: dict[str, Any] | None,
    refreshed: dict[str, Any],
) -> dict[str, Any]:
    """Carry validated IndexedDB data into a fast shutdown snapshot."""

    if not previous:
        return refreshed
    preserved = {
        str(origin.get("origin")): origin.get("indexedDB")
        for origin in previous.get("origins", [])
        if isinstance(origin, dict) and origin.get("indexedDB") is not None
    }
    if not preserved:
        return refreshed
    merged = dict(refreshed)
    origins = [dict(origin) for origin in refreshed.get("origins", [])]
    by_name = {
        str(origin.get("origin")): origin
        for origin in origins
        if isinstance(origin, dict)
    }
    for origin_name, indexed_db in preserved.items():
        origin = by_name.get(origin_name)
        if origin is None:
            origin = {"origin": origin_name, "localStorage": []}
            origins.append(origin)
        origin["indexedDB"] = indexed_db
    merged["origins"] = origins
    return merged
