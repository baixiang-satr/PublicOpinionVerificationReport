"""Stable models for guest probing and reusable authentication profiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AuthStatus(StrEnum):
    UNKNOWN = "unknown"
    PROBING = "probing"
    GUEST_OK = "guest_ok"
    AUTH_REQUIRED = "auth_required"
    CHALLENGE = "challenge"
    WAITING_USER = "waiting_user"
    VALIDATING = "validating"
    VALID = "valid"
    EXPIRED = "expired"
    INVALID_URL = "invalid_url"
    ACCESS_BLOCKED = "access_blocked"
    ERROR = "error"


@dataclass(frozen=True)
class PlatformAuthPolicy:
    platform_key: str
    display_name: str
    probe_url: str
    host_suffixes: tuple[str, ...]
    auth_scope: str
    phone_assist: bool = True


@dataclass(frozen=True)
class AuthProbeResult:
    platform_key: str
    status: AuthStatus
    checked_at: datetime
    original_url: str
    final_url: str | None = None
    barrier_code: str | None = None
    message: str = ""
    used_saved_state: bool = False


@dataclass(frozen=True)
class AuthProfile:
    profile_id: str
    platform_key: str
    auth_scope: str
    status: AuthStatus = AuthStatus.UNKNOWN
    masked_phone: str | None = None
    validated_at: str | None = None
    validation_url: str | None = None
    last_error_code: str | None = None
    last_message: str = ""
    state_filename: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["status"] = self.status.value
        return values

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "AuthProfile":
        return cls(
            profile_id=str(values["profile_id"]),
            platform_key=str(values["platform_key"]),
            auth_scope=str(values.get("auth_scope") or values["platform_key"]),
            status=AuthStatus(values.get("status", AuthStatus.UNKNOWN.value)),
            masked_phone=values.get("masked_phone"),
            validated_at=values.get("validated_at"),
            validation_url=values.get("validation_url"),
            last_error_code=values.get("last_error_code"),
            last_message=str(values.get("last_message") or ""),
            state_filename=values.get("state_filename"),
            schema_version=int(values.get("schema_version", 1)),
        )


def mask_phone(phone: str | None) -> str | None:
    digits = "".join(character for character in (phone or "") if character.isdigit())
    if not digits:
        return None
    if len(digits) <= 7:
        return f"{digits[:2]}***{digits[-2:]}"
    return f"{digits[:3]}****{digits[-4:]}"
