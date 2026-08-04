"""Offline one-machine-one-code licensing (Ed25519 signed, machine-bound)."""

from src.license.crypto import (
    LicenseBadSignature,
    LicenseCodeMalformed,
    LicenseError,
    encode_code,
    generate_keypair,
    inspect_code,
    verify_code,
)
from src.license.fingerprint import (
    MachineFingerprintError,
    is_valid_short_code,
    machine_short_code,
    normalize_short_code,
)
from src.license.manager import LicenseManager
from src.license.models import LicenseInfo, LicensePayload, LicenseStatus

__all__ = [
    "LicenseBadSignature",
    "LicenseCodeMalformed",
    "LicenseError",
    "LicenseInfo",
    "LicenseManager",
    "LicensePayload",
    "LicenseStatus",
    "MachineFingerprintError",
    "encode_code",
    "generate_keypair",
    "inspect_code",
    "is_valid_short_code",
    "machine_short_code",
    "normalize_short_code",
    "verify_code",
]
