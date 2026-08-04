"""License payload models and status enumeration.

授权码（一机一码）核心数据契约：
- ``LicensePayload`` 是被 Ed25519 签名的 JSON 内容，由卖方签发工具生成；
- ``LicenseInfo`` 是桥接层暴露给前端的视图模型；
- ``LicenseStatus`` 枚举所有校验结果，前端据此展示提示。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel


class LicenseStatus(str, Enum):
    """License verification outcome."""

    VALID = "valid"
    NOT_ACTIVATED = "not_activated"
    MALFORMED = "malformed"
    BAD_SIGNATURE = "bad_signature"
    MACHINE_MISMATCH = "machine_mismatch"
    EXPIRED = "expired"
    FINGERPRINT_ERROR = "fingerprint_error"


class LicensePayload(BaseModel):
    """Signed license content, serialized to canonical JSON then Ed25519-signed."""

    v: int = 1
    license_id: str
    licensee: str
    machine_id: str
    issued_at: datetime
    expires_at: datetime
    product: str = "poir"

    def canonical_json(self) -> bytes:
        """Stable serialization used for signing and verification."""

        return self.model_dump_json().encode("utf-8")

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(tz=timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return moment >= expires


class LicenseInfo(BaseModel):
    """View model exposed to the web UI through the bridge."""

    activated: bool
    status: LicenseStatus
    message: str
    machine_code: str
    licensee: str | None = None
    license_id: str | None = None
    expires_at: str | None = None

    def to_payload(self) -> dict:
        """JSON-safe dict for pywebview js_api（附带 ok 便于前端判断）。"""

        payload = self.model_dump(mode="json")
        payload["ok"] = self.activated
        return payload


def build_info(
    status: LicenseStatus,
    machine_code: str,
    *,
    payload: LicensePayload | None = None,
    message: str | None = None,
) -> LicenseInfo:
    """Assemble a ``LicenseInfo`` with a localized default message."""

    expires_text = payload.expires_at.date().isoformat() if payload else None
    messages = {
        LicenseStatus.VALID: f"已授权给 {payload.licensee}，有效期至 {expires_text}。" if payload else "授权有效。",
        LicenseStatus.NOT_ACTIVATED: "尚未激活，请输入授权码完成激活。",
        LicenseStatus.MALFORMED: "授权码格式无效，请核对后重新输入。",
        LicenseStatus.BAD_SIGNATURE: "授权码校验失败，请确认授权码完整无误。",
        LicenseStatus.MACHINE_MISMATCH: "该授权码与本机不匹配，一机一码无法跨机使用。",
        LicenseStatus.EXPIRED: f"授权已于 {expires_text} 到期，请联系供应商续期。" if expires_text else "授权已到期。",
        LicenseStatus.FINGERPRINT_ERROR: "无法读取本机标识，请确认在受支持的 Windows 环境运行。",
    }
    return LicenseInfo(
        activated=status is LicenseStatus.VALID,
        status=status,
        message=message or messages[status],
        machine_code=machine_code,
        licensee=payload.licensee if payload else None,
        license_id=payload.license_id if payload else None,
        expires_at=expires_text,
    )


__all__ = [
    "LicenseInfo",
    "LicensePayload",
    "LicenseStatus",
    "build_info",
]
