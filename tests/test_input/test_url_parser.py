from src.input.url_parser import build_url_tasks
from src.utils.url_utils import UrlNormalizationError, normalize_url


def test_build_url_tasks_keeps_first_seen_order_and_removes_fragments() -> None:
    tasks, rejected = build_url_tasks(
        [
            "First: https://Example.com/a#section.",
            "Duplicate: https://example.com/a",
            "Second: https://example.com/b?x=1",
        ]
    )

    assert [task.evidence_id for task in tasks] == [1, 2]
    assert [task.normalized_url for task in tasks] == ["https://example.com/a", "https://example.com/b?x=1"]
    assert rejected == ["https://example.com/a"]


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
