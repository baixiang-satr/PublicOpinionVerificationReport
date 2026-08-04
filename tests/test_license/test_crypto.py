"""Ed25519 license-code signing / verification tests (fully offline)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.license.crypto import (
    LicenseBadSignature,
    LicenseCodeMalformed,
    encode_code,
    generate_keypair,
    inspect_code,
    parse_code,
    verify_code,
)
from src.license.models import LicensePayload

_NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _payload(**overrides) -> LicensePayload:
    data = {
        "license_id": "L-2026-0001",
        "licensee": "示例客户",
        "machine_id": "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA",
        "issued_at": _NOW,
        "expires_at": _NOW + timedelta(days=365),
    }
    data.update(overrides)
    return LicensePayload(**data)


@pytest.fixture()
def keypair() -> tuple[str, str]:
    return generate_keypair()


def test_roundtrip_sign_and_verify(keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    code = encode_code(_payload(), private_pem)
    assert code.startswith("POIR1.")
    payload = verify_code(code, public_pem)
    assert payload.license_id == "L-2026-0001"
    assert payload.licensee == "示例客户"
    assert payload.product == "poir"


def test_tampered_payload_fails_verification(keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    code = encode_code(_payload(), private_pem)
    prefix, payload_part, signature_part = code.split(".")
    # 翻转 payload 段的字符：若解析仍成功则签名必然不匹配
    flipped = ("A" if payload_part[0] != "A" else "B") + payload_part[1:]
    tampered = ".".join((prefix, flipped, signature_part))
    with pytest.raises((LicenseBadSignature, LicenseCodeMalformed)):
        verify_code(tampered, public_pem)


def test_wrong_public_key_fails_verification(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair
    _, other_public = generate_keypair()
    code = encode_code(_payload(), private_pem)
    with pytest.raises(LicenseBadSignature):
        verify_code(code, other_public)


@pytest.mark.parametrize(
    "code",
    [
        "",
        "POIR1",
        "POIR1.onlyone",
        "POIR1.a.b.c",
        "WRONG.abc.def",
        "POIR1..def",
        "POIR1.abc.",
        "POIR1.!!!.def",
    ],
)
def test_malformed_codes_rejected(code: str, keypair: tuple[str, str]) -> None:
    _, public_pem = keypair
    with pytest.raises(LicenseCodeMalformed):
        verify_code(code, public_pem)


def test_parse_code_splits_parts(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair
    code = encode_code(_payload(), private_pem)
    payload_bytes, signature = parse_code(code)
    assert len(signature) == 64  # Ed25519 签名固定 64 字节
    assert b"L-2026-0001" in payload_bytes


def test_inspect_code_decodes_without_verification(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair
    code = encode_code(_payload(licensee="仅解码客户"), private_pem)
    payload = inspect_code(code)
    assert payload.licensee == "仅解码客户"
