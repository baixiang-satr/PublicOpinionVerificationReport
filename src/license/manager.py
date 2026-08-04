"""License activation, verification and persistence.

启动/操作时经 ``status()`` 校验授权；激活经 ``activate(code)``。
授权码原文（``POIR1.…``）经 Windows DPAPI 加密后落盘，换机复制无效：
- 签名由应用内嵌公钥验签（防伪造）；
- payload.machine_id 与本机短码比对（防一码多机）；
- expires_at 与当前时间比对（防超期使用）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path

from src.auth.protection import StateProtectionError, StateProtector, default_state_protector
from src.config.settings import _local_app_data_root
from src.license.crypto import (
    EMBEDDED_PUBLIC_KEY_PEM,
    LicenseBadSignature,
    LicenseCodeMalformed,
    verify_code,
)
from src.license.fingerprint import MachineFingerprintError, machine_short_code, normalize_short_code
from src.license.models import LicenseInfo, LicensePayload, LicenseStatus, build_info

_LICENSE_FILENAME = "license.dat"


class LicenseManager:
    """Activate, persist and evaluate the offline machine-bound license."""

    def __init__(
        self,
        storage_path: Path | None = None,
        *,
        public_key_pem: str = EMBEDDED_PUBLIC_KEY_PEM,
        protector: StateProtector | None = None,
        machine_code_provider: Callable[[], str] = machine_short_code,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage_path = storage_path or (_local_app_data_root() / _LICENSE_FILENAME)
        self._public_key_pem = public_key_pem
        self._protector = protector
        self._machine_code_provider = machine_code_provider
        self._now_provider = now_provider or (lambda: datetime.now(tz=timezone.utc))

    # ── 查询 ──────────────────────────────────────────────────────────
    def status(self) -> LicenseInfo:
        """Return the current license state for the UI / bridge."""

        machine_code = self._machine_code()
        if machine_code is None:
            return build_info(LicenseStatus.FINGERPRINT_ERROR, "")
        payload = self._load_payload()
        if payload is None:
            return build_info(LicenseStatus.NOT_ACTIVATED, machine_code)
        return self._evaluate(payload, machine_code)

    def is_valid(self) -> bool:
        """Fast gate used by bridge operations."""

        return self.status().activated

    # ── 激活 / 注销 ───────────────────────────────────────────────────
    def activate(self, code: str) -> LicenseInfo:
        """Verify and persist a license code entered by the user."""

        machine_code = self._machine_code()
        if machine_code is None:
            return build_info(LicenseStatus.FINGERPRINT_ERROR, "")
        try:
            payload = verify_code(code, self._public_key_pem)
        except LicenseCodeMalformed:
            return build_info(LicenseStatus.MALFORMED, machine_code)
        except LicenseBadSignature:
            return build_info(LicenseStatus.BAD_SIGNATURE, machine_code)
        info = self._evaluate(payload, machine_code)
        if info.activated:
            self._store(code)
        return info

    def deactivate(self) -> LicenseInfo:
        """Remove the stored license (vendor support / testing)."""

        self._storage_path.unlink(missing_ok=True)
        return self.status()

    # ── 内部 ──────────────────────────────────────────────────────────
    def _machine_code(self) -> str | None:
        try:
            return self._machine_code_provider()
        except MachineFingerprintError:
            return None

    def _evaluate(self, payload: LicensePayload, machine_code: str) -> LicenseInfo:
        try:
            bound_code = normalize_short_code(payload.machine_id)
        except ValueError:
            return build_info(LicenseStatus.MACHINE_MISMATCH, machine_code, payload=payload)
        if bound_code != machine_code:
            return build_info(LicenseStatus.MACHINE_MISMATCH, machine_code, payload=payload)
        if payload.is_expired(self._now_provider()):
            return build_info(LicenseStatus.EXPIRED, machine_code, payload=payload)
        return build_info(LicenseStatus.VALID, machine_code, payload=payload)

    def _load_payload(self) -> LicensePayload | None:
        """Load and verify the stored license; corruption → 未激活."""

        if not self._storage_path.is_file():
            return None
        try:
            blob = self._storage_path.read_bytes()
            protector = self._get_protector()
            raw = protector.unprotect(blob) if protector else blob
            return verify_code(raw.decode("utf-8"), self._public_key_pem)
        except (OSError, StateProtectionError, UnicodeDecodeError, LicenseCodeMalformed, LicenseBadSignature):
            return None

    def _store(self, code: str) -> None:
        raw = code.strip().encode("utf-8")
        protector = self._get_protector()
        blob = protector.protect(raw) if protector else raw
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_bytes(blob)

    def _get_protector(self) -> StateProtector | None:
        """DPAPI 保护器（惰性创建）；非 Windows 或 DPAPI 不可用时明文存储。

        明文回退不削弱安全性：授权码本身由 Ed25519 签名保护，无法伪造。
        """

        if getattr(self, "_protector_unavailable", False):
            return None
        if self._protector is None and os.name == "nt":
            try:
                self._protector = default_state_protector()
            except StateProtectionError:
                self._protector_unavailable = True
                return None
        return self._protector


__all__ = ["LicenseManager"]
