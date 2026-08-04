"""LicenseManager tests — 临时目录 + 假保护器 + 注入机器码/时间，全离线。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.license.crypto import encode_code, generate_keypair
from src.license.manager import LicenseManager
from src.license.models import LicensePayload, LicenseStatus

_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
_MACHINE = "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA"
_OTHER_MACHINE = "BBBB-BBBB-BBBB-BBBB-BBBB-BBBB"


class _PassthroughProtector:
    """测试用保护器：字节透传并记录调用。"""

    def __init__(self) -> None:
        self.protect_calls = 0
        self.unprotect_calls = 0

    def protect(self, plaintext: bytes) -> bytes:
        self.protect_calls += 1
        return plaintext

    def unprotect(self, ciphertext: bytes) -> bytes:
        self.unprotect_calls += 1
        return ciphertext


@pytest.fixture()
def keypair() -> tuple[str, str]:
    return generate_keypair()


@pytest.fixture()
def protector() -> _PassthroughProtector:
    return _PassthroughProtector()


def _make_manager(
    tmp_path: Path,
    public_pem: str,
    protector: _PassthroughProtector,
    *,
    machine: str = _MACHINE,
    now: datetime = _NOW,
) -> LicenseManager:
    return LicenseManager(
        tmp_path / "license.dat",
        public_key_pem=public_pem,
        protector=protector,
        machine_code_provider=lambda: machine,
        now_provider=lambda: now,
    )


def _issue_code(
    private_pem: str,
    *,
    machine: str = _MACHINE,
    expires: datetime = _NOW + timedelta(days=365),
    licensee: str = "示例客户",
) -> str:
    payload = LicensePayload(
        license_id="L-2026-0001",
        licensee=licensee,
        machine_id=machine,
        issued_at=_NOW - timedelta(days=1),
        expires_at=expires,
    )
    return encode_code(payload, private_pem)


def test_status_not_activated_when_no_file(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    manager = _make_manager(tmp_path, keypair[1], protector)
    info = manager.status()
    assert info.status is LicenseStatus.NOT_ACTIVATED
    assert not info.activated
    assert info.machine_code == _MACHINE
    assert not manager.is_valid()


def test_activate_valid_persists_and_reloads(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    private_pem, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    info = manager.activate(_issue_code(private_pem))
    assert info.status is LicenseStatus.VALID
    assert info.activated
    assert info.licensee == "示例客户"
    assert info.expires_at == (_NOW + timedelta(days=365)).date().isoformat()
    assert protector.protect_calls == 1
    # 新实例（模拟重启）从磁盘恢复，验签后仍有效
    reloaded = _make_manager(tmp_path, public_pem, protector)
    again = reloaded.status()
    assert again.activated
    assert again.license_id == "L-2026-0001"
    assert protector.unprotect_calls == 1


def test_activate_malformed_and_bad_signature(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    _, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    assert manager.activate("not-a-license").status is LicenseStatus.MALFORMED
    other_private, _ = generate_keypair()
    forged = _issue_code(other_private)
    assert manager.activate(forged).status is LicenseStatus.BAD_SIGNATURE
    assert not (tmp_path / "license.dat").exists()


def test_activate_machine_mismatch_not_stored(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    private_pem, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    info = manager.activate(_issue_code(private_pem, machine=_OTHER_MACHINE))
    assert info.status is LicenseStatus.MACHINE_MISMATCH
    assert not info.activated
    assert not (tmp_path / "license.dat").exists()


def test_activate_expired_rejected(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    private_pem, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    info = manager.activate(_issue_code(private_pem, expires=_NOW - timedelta(days=1)))
    assert info.status is LicenseStatus.EXPIRED
    assert not (tmp_path / "license.dat").exists()


def test_stored_license_expires_with_time(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    private_pem, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    code = _issue_code(private_pem, expires=_NOW + timedelta(days=30))
    assert manager.activate(code).activated
    later = _make_manager(tmp_path, public_pem, protector, now=_NOW + timedelta(days=31))
    assert later.status().status is LicenseStatus.EXPIRED


def test_stored_license_machine_mismatch(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    private_pem, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    assert manager.activate(_issue_code(private_pem)).activated
    # 同一存储文件搬到另一台机器
    moved = _make_manager(tmp_path, public_pem, protector, machine=_OTHER_MACHINE)
    assert moved.status().status is LicenseStatus.MACHINE_MISMATCH


def test_corrupted_storage_treated_as_not_activated(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    manager = _make_manager(tmp_path, keypair[1], protector)
    (tmp_path / "license.dat").write_bytes(b"corrupted-bytes")
    assert manager.status().status is LicenseStatus.NOT_ACTIVATED


def test_deactivate_removes_license(
    tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector
) -> None:
    private_pem, public_pem = keypair
    manager = _make_manager(tmp_path, public_pem, protector)
    assert manager.activate(_issue_code(private_pem)).activated
    info = manager.deactivate()
    assert info.status is LicenseStatus.NOT_ACTIVATED
    assert not (tmp_path / "license.dat").exists()


def test_fingerprint_error_status(tmp_path: Path, keypair: tuple[str, str], protector: _PassthroughProtector) -> None:
    from src.license.fingerprint import MachineFingerprintError

    def _raise() -> str:
        raise MachineFingerprintError("boom")

    manager = LicenseManager(
        tmp_path / "license.dat",
        public_key_pem=keypair[1],
        protector=protector,
        machine_code_provider=_raise,
    )
    assert manager.status().status is LicenseStatus.FINGERPRINT_ERROR
    assert manager.activate("whatever").status is LicenseStatus.FINGERPRINT_ERROR
