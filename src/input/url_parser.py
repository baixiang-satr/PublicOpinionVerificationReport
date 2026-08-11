"""Extract and normalize HTTP(S) URLs while preserving every input occurrence."""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.models import UrlTask
from src.utils.url_utils import UrlNormalizationError, extract_urls, normalize_url


def build_url_tasks(
    values: Iterable[str],
    start_evidence_id: int = 1,
    *,
    dedupe: bool = False,
) -> tuple[list[UrlTask], list[str]]:
    """Return URL tasks in source order plus invalid source tokens.

    默认保留重复（按源顺序）；``dedupe=True`` 时每个 normalized_url 只保留
    首次出现，证据编号随去重后的列表连续分配。
    """

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
            if dedupe and normalized in seen:
                continue
            seen.add(normalized)
            tasks.append(UrlTask(start_evidence_id + len(tasks), candidate, normalized))
    return tasks, rejected


def find_duplicate_urls(
    values: Iterable[str],
    *,
    max_examples: int = 3,
) -> tuple[int, tuple[str, ...]]:
    """按 normalized_url 完全一致判定重复：返回（重复条数, 示例 URL）。

    重复条数=超出首次出现的多余份数；示例取重复 URL 的原始形态（去重、
    最多 ``max_examples`` 条）。无效值不参与统计。
    """

    seen: set[str] = set()
    duplicates = 0
    examples: list[str] = []
    for value in values:
        text = str(value).strip()
        for candidate in extract_urls(text):
            try:
                normalized = normalize_url(candidate)
            except UrlNormalizationError:
                continue
            if normalized in seen:
                duplicates += 1
                if len(examples) < max_examples and candidate not in examples:
                    examples.append(candidate)
            else:
                seen.add(normalized)
    return duplicates, tuple(examples)
