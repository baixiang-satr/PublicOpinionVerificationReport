from pathlib import Path

import pytest

from src.domain.overrides import ManualOverride
from src.services.override_store import (
    OVERRIDES_FILE_NAME,
    ManualOverrideStore,
)


def test_override_store_round_trip(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    store.set_field(3, "title", "人工标题")
    store.set_field(3, "content", "人工正文")
    store.set_field(3, "published_at", "2026-07-01 08:30:00")
    store.set_primary_screenshot(3, "003_manual.png")
    store.set_author_screenshot(3, "003_author.png")
    store.set_attachments(3, ["003_extra.png"])
    store.set_note(3, "视频号人工补录")

    loaded = ManualOverrideStore(tmp_path).load()

    override = loaded.get(3)
    assert override is not None
    assert override.values["title"] == "人工标题"
    assert override.values["content"] == "人工正文"
    assert override.values["published_at"] == "2026-07-01 08:30:00"
    assert override.primary_screenshot_name == "003_manual.png"
    assert override.author_screenshot_name == "003_author.png"
    assert override.attachment_names == ["003_extra.png"]
    assert override.note == "视频号人工补录"
    assert override.updated_at is not None
    assert not (tmp_path / (OVERRIDES_FILE_NAME + ".tmp")).exists()


def test_override_store_drops_empty_overrides(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    store.set_field(1, "title", "有内容")
    store.set_field(2, "title", "   ")  # blank clears the field

    loaded = ManualOverrideStore(tmp_path).load()

    assert loaded.get(1) is not None
    assert loaded.get(2) is None


def test_override_store_rejects_unknown_field(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    with pytest.raises(KeyError):
        store.set_field(1, "not_a_field", "x")


def test_override_store_rejects_unsafe_screenshot_name(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    with pytest.raises(Exception):
        store.set_primary_screenshot(1, "../evil.png")


def test_override_store_rejects_unsafe_author_screenshot_name(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    with pytest.raises(Exception):
        store.set_author_screenshot(1, "../evil.png")


def test_override_store_drops_author_only_empty_override(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    store.set_author_screenshot(2, None)
    assert store.get(2) is None
    store.set_author_screenshot(2, "002_author.png")
    store.set_author_screenshot(2, None)  # clearing empties the override
    loaded = ManualOverrideStore(tmp_path).load()
    assert loaded.get(2) is None


def test_override_store_load_ignores_malformed_entries(tmp_path: Path) -> None:
    (tmp_path / OVERRIDES_FILE_NAME).write_text(
        (
            '{"schema_version": 1, "overrides": ['
            '{"evidence_id": "bad"}, '
            '{"evidence_id": 5, "values": {"title": "ok", "rogue": 1}, '
            '"primary_screenshot_name": "..\\\\bad.png"},'
            'null]}'
        ),
        encoding="utf-8",
    )
    loaded = ManualOverrideStore(tmp_path).load()
    assert loaded.get(5) is not None
    override = loaded.get(5)
    assert override is not None
    assert override.values == {"title": "ok"}
    assert override.primary_screenshot_name is None


def test_override_remove_persists(tmp_path: Path) -> None:
    store = ManualOverrideStore(tmp_path)
    store.set_field(1, "title", "x")
    store.remove(1)
    loaded = ManualOverrideStore(tmp_path).load()
    assert loaded.get(1) is None


def test_manual_override_is_empty_logic() -> None:
    override = ManualOverride(evidence_id=1)
    assert override.is_empty()
    override.set_value("title", "  ")
    assert override.is_empty()
    override.author_screenshot_name = "001_author.png"
    assert not override.is_empty()
    override.author_screenshot_name = None
    assert override.is_empty()
    override.set_value("title", "实际标题")
    assert not override.is_empty()
