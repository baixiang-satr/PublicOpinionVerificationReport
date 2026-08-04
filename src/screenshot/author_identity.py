"""Rendered identity helpers kept separate from screenshot orchestration."""

from __future__ import annotations

from typing import Any

from src.screenshot.author_evidence import normalize_identity


def signals_contain_expected(
    signals: dict[str, Any],
    expected_name: str | None,
) -> bool:
    """Require the target identity in rendered content, not title alone."""

    expected_key = normalize_identity(expected_name)
    if not expected_key:
        return True
    values: list[str] = []
    names = signals.get("headerNames")
    if isinstance(names, list):
        values.extend(str(value) for value in names)
    values.extend(
        (str(signals.get("headerName") or ""), str(signals.get("body") or ""))
    )
    return any(expected_key in normalize_identity(value) for value in values)
