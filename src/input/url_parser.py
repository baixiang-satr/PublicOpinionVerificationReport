"""Extract, normalize and stably deduplicate HTTP(S) URLs from arbitrary text."""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.models import UrlTask
from src.utils.url_utils import UrlNormalizationError, extract_urls, normalize_url


def build_url_tasks(values: Iterable[str], start_evidence_id: int = 1) -> tuple[list[UrlTask], list[str]]:
    """Return first-seen URL tasks plus invalid or duplicate source tokens."""

    tasks: list[UrlTask] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        candidates = extract_urls(text)
        if text and not candidates:
            rejected.append(text)
            continue
        for candidate in candidates:
            try:
                normalized = normalize_url(candidate)
            except UrlNormalizationError:
                rejected.append(candidate)
                continue
            if normalized in seen:
                rejected.append(candidate)
                continue
            seen.add(normalized)
            tasks.append(UrlTask(start_evidence_id + len(tasks), candidate, normalized))
    return tasks, rejected
