"""Extract and normalize HTTP(S) URLs while preserving every input occurrence."""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.models import UrlTask
from src.utils.url_utils import UrlNormalizationError, extract_urls, normalize_url


def build_url_tasks(values: Iterable[str], start_evidence_id: int = 1) -> tuple[list[UrlTask], list[str]]:
    """Return URL tasks in source order plus invalid source tokens."""

    tasks: list[UrlTask] = []
    rejected: list[str] = []
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
            tasks.append(UrlTask(start_evidence_id + len(tasks), candidate, normalized))
    return tasks, rejected
