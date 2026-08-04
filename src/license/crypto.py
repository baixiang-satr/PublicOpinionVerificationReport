"""Ed25519 signing and license-code encoding for offline one-machine-one-code licensing.

授权码格式::

    POIR1.<base64url(payload_json)>.<base64url(ed25519_signature)>

卖方持有私钥用 ``encode_code`` 签发；应用内嵌公钥用 ``verify_code`` 验签。
签名直接覆盖 payload 原始字节，验签通过后再解析为 ``LicensePayload``，
避免任何重序列化不一致。
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)
from pydantic import ValidationError

from src.license.models import LicensePayload

CODE_PREFIX = "POIR1"
_CODE_PARTS = 3

# 开发占位公钥：由 ``python tools/license_admin.py genkey`` 生成的开发密钥对写入
# （配套私钥在 keys/dev/，仅供开发联调）。
# 发布正式版前务必替换为卖方正式公钥，私钥离线保管、绝不随包分发。
EMBEDDED_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA954OBqi5Q0Y91Q5E470hN8w1Tweg9iFJeB3P5EitfAU=
-----END PUBLIC KEY-----"""


class LicenseError(RuntimeError):
    """Base class for license crypto failures."""


class LicenseCodeMalformed(LicenseError):
    """The license code text cannot be parsed into payload + signature."""


class LicenseBadSignature(LicenseError):
    """The license signature does not verify against the public key."""


def generate_keypair() -> tuple[str, str]:
    """Return ``(private_pem, public_pem)`` for a fresh Ed25519 keypair."""

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        Encoding.PEM,
        PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def encode_code(payload: LicensePayload, private_key_pem: str) -> str:
    """Sign ``payload`` and return the full license code text."""

    private_key = load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise LicenseError("私钥必须是 Ed25519 私钥（PEM/PKCS8）。")
    payload_bytes = payload.canonical_json()
    signature = private_key.sign(payload_bytes)
    return ".".join(
        (
            CODE_PREFIX,
            _b64url_encode(payload_bytes),
            _b64url_encode(signature),
        )
    )


def parse_code(code: str) -> tuple[bytes, bytes]:
    """Split a license code into ``(payload_bytes, signature)`` without verifying."""

    parts = (code or "").strip().split(".")
    if len(parts) != _CODE_PARTS or parts[0] != CODE_PREFIX or not parts[1] or not parts[2]:
        raise LicenseCodeMalformed("授权码格式无效。")
    try:
        payload_bytes = _b64url_decode(parts[1])
        signature = _b64url_decode(parts[2])
    except (ValueError, binascii.Error) as error:
        raise LicenseCodeMalformed("授权码包含非法字符。") from error
    return payload_bytes, signature


def decode_payload(payload_bytes: bytes) -> LicensePayload:
    """Parse raw payload bytes into a validated ``LicensePayload``."""

    try:
        return LicensePayload.model_validate_json(payload_bytes)
    except ValidationError as error:
        raise LicenseCodeMalformed("授权码内容无法解析。") from error


def verify_code(code: str, public_key_pem: str) -> LicensePayload:
    """Verify a license code against ``public_key_pem`` and return its payload."""

    payload_bytes, signature = parse_code(code)
    try:
        public_key = load_pem_public_key(public_key_pem.encode("ascii"))
    except ValueError as error:
        raise LicenseError("内嵌公钥无效，请联系供应商。") from error
    if not isinstance(public_key, Ed25519PublicKey):
        raise LicenseError("内嵌公钥必须是 Ed25519 公钥。")
    try:
        public_key.verify(signature, payload_bytes)
    except InvalidSignature as error:
        raise LicenseBadSignature("授权码签名校验失败。") from error
    return decode_payload(payload_bytes)


def inspect_code(code: str) -> LicensePayload:
    """Decode a license code without verifying the signature (vendor tooling)."""

    payload_bytes, _ = parse_code(code)
    return decode_payload(payload_bytes)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    # validate=True：字母表外字符（如 "!"）直接报错，而非静默丢弃
    return base64.b64decode(text + padding, altchars=b"-_", validate=True)


__all__ = [
    "CODE_PREFIX",
    "EMBEDDED_PUBLIC_KEY_PEM",
    "LicenseBadSignature",
    "LicenseCodeMalformed",
    "LicenseError",
    "decode_payload",
    "encode_code",
    "generate_keypair",
    "inspect_code",
    "parse_code",
    "verify_code",
]
