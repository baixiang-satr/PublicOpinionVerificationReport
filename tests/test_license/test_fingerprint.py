"""Machine fingerprint tests — 注册表/WMI 均 monkeypatch，不触真实系统。"""

from __future__ import annotations

import pytest

from src.license import fingerprint
from src.license.fingerprint import (
    MachineFingerprintError,
    is_valid_short_code,
    machine_fingerprint,
    machine_short_code,
    normalize_short_code,
)

_GUID_A = "11111111-2222-3333-4444-555555555555"
_UUID_A = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"


@pytest.fixture(autouse=True)
def _reset_fingerprint_cache():
    fingerprint._reset_cache()
    yield
    fingerprint._reset_cache()


def _stub_components(monkeypatch: pytest.MonkeyPatch, guid: str = _GUID_A, uuid: str = _UUID_A) -> None:
    monkeypatch.setattr(fingerprint, "_machine_guid", lambda: guid)
    monkeypatch.setattr(fingerprint, "_system_uuid", lambda: uuid)


def test_fingerprint_stable_and_short_code_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_components(monkeypatch)
    first = machine_fingerprint()
    second = machine_fingerprint()
    assert first == second
    assert len(first) == 64  # SHA-256 hex
    short = machine_short_code()
    assert len(short) == 29  # 24 hex + 5 dashes
    raw = first[:24].upper()
    assert short == "-".join(raw[i : i + 4] for i in range(0, 24, 4))


def test_fingerprint_changes_with_any_component(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_components(monkeypatch)
    base = machine_short_code()
    fingerprint._reset_cache()
    _stub_components(monkeypatch, guid="99999999-2222-3333-4444-555555555555")
    assert machine_short_code() != base
    fingerprint._reset_cache()
    _stub_components(monkeypatch, uuid="FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF")
    assert machine_short_code() != base


def test_non_windows_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "posix")
    with pytest.raises(MachineFingerprintError):
        machine_fingerprint()


@pytest.mark.parametrize(
    ("raw", "expected_prefix"),
    [
        ("a" * 24, "AAAA-AAAA"),
        ("AAAA-AAAA-AAAA-AAAA-AAAA-AAAA", "AAAA-AAAA"),
        ("aaaa bbbb cccc dddd eeee ffff", "AAAA-BBBB"),
    ],
)
def test_normalize_short_code_accepts_common_forms(raw: str, expected_prefix: str) -> None:
    normalized = normalize_short_code(raw)
    assert normalized.startswith(expected_prefix)
    assert len(normalized) == 29


@pytest.mark.parametrize("raw", ["", "abc", "Z" * 24, "AAAA-AAAA-AAAA", "A" * 25])
def test_normalize_short_code_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_short_code(raw)
    assert not is_valid_short_code(raw)
