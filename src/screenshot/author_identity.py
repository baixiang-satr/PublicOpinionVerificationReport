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


def best_header_name(
    signals: dict[str, Any],
    expected_name: str | None,
) -> str | None:
    """Prefer a profile-header candidate matching the content-page author."""

    raw_names = signals.get("headerNames")
    candidates = (
        [str(value).strip() for value in raw_names if str(value).strip()]
        if isinstance(raw_names, list)
        else []
    )
    fallback = str(signals.get("headerName") or "").strip()
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    expected_key = normalize_identity(expected_name)
    if expected_key:
        for candidate in candidates:
            candidate_key = normalize_identity(candidate)
            if candidate_key and (
                expected_key in candidate_key or candidate_key in expected_key
            ):
                return candidate
        # Toutiao and similar desktop profile pages can expose the signed-in
        # viewer's city/name in the global navigation under a broad
        # ``user-name`` class while the actual profile identity is rendered
        # without a stable selector.  The page title is still scoped to the
        # candidate URL (for example "作者名的头条主页").  Accept the already
        # extracted author only when that full identity is present in title;
        # the later identity gate still checks the body and page URL.
        title_key = normalize_identity(str(signals.get("title") or ""))
        body_key = normalize_identity(str(signals.get("body") or ""))
        if title_key and expected_key in title_key and expected_key in body_key:
            return expected_name.strip() if expected_name else None
    return candidates[0] if candidates else None
