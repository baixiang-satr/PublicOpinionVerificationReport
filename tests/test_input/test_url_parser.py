from src.input.url_parser import build_url_tasks, find_duplicate_urls
from src.utils.url_utils import UrlNormalizationError, normalize_url


def test_build_url_tasks_keeps_source_order_duplicates_and_removes_fragments() -> None:
    tasks, rejected = build_url_tasks(
        [
            "First: https://Example.com/a#section.",
            "Duplicate: https://example.com/a",
            "Second: https://example.com/b?x=1",
        ]
    )

    assert [task.evidence_id for task in tasks] == [1, 2, 3]
    assert [task.normalized_url for task in tasks] == [
        "https://example.com/a",
        "https://example.com/a",
        "https://example.com/b?x=1",
    ]
    assert rejected == []


def test_normalize_url_rejects_invalid_ports() -> None:
    try:
        normalize_url("https://example.com:not-a-port")
    except UrlNormalizationError:
        return
    raise AssertionError("Invalid ports must be rejected.")


def test_build_url_tasks_counts_non_url_values_as_rejected() -> None:
    tasks, rejected = build_url_tasks(["https://example.com/a", "not a URL", "ftp://example.com/file"])

    assert [task.normalized_url for task in tasks] == ["https://example.com/a"]
    assert rejected == ["not a URL", "ftp://example.com/file"]


def test_build_url_tasks_dedupe_keeps_first_occurrence() -> None:
    tasks, rejected = build_url_tasks(
        [
            "First: https://Example.com/a#section.",
            "Duplicate: https://example.com/a",
            "Second: https://example.com/b?x=1",
            "Third: https://example.com/a",
        ],
        dedupe=True,
    )

    assert [task.evidence_id for task in tasks] == [1, 2]
    assert [task.normalized_url for task in tasks] == [
        "https://example.com/a",
        "https://example.com/b?x=1",
    ]
    assert rejected == []


def test_find_duplicate_urls_counts_extra_copies_with_examples() -> None:
    count, examples = find_duplicate_urls(
        [
            "https://example.com/a",
            "https://Example.com/a#frag",  # 规范化后与首条一致
            "https://example.com/a",
            "https://example.com/b",
            "not a url",
        ]
    )

    assert count == 2
    assert examples
    assert all("example.com/a" in example.casefold() for example in examples)


def test_find_duplicate_urls_no_duplicates() -> None:
    count, examples = find_duplicate_urls(["https://example.com/a", "https://example.com/b"])

    assert count == 0
    assert examples == ()
