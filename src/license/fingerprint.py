"""Windows machine fingerprint for one-machine-one-code licensing.

指纹来源（与规划一致）：
- 注册表 ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``
- WMI ``Win32_ComputerSystemProduct.UUID``（经 pywin32 读取）

两者加盐后 SHA-256，取前 24 位 hex 作为「机器短码」展示与绑定。
短码 96 bit 熵，碰撞概率可忽略，避免长/短码两套比对逻辑。
"""

from __future__ import annotations

import hashlib
import os
import re

_HASH_SALT = "PublicOpinionVerificationReport/license/v1"
_SHORT_CODE_LEN = 24
_GROUPED_PATTERN = re.compile(r"^[0-9A-F]{4}(-[0-9A-F]{4}){5}$")

_fingerprint_cache: str | None = None


class MachineFingerprintError(RuntimeError):
    """Raised when the machine fingerprint cannot be determined."""


def machine_fingerprint() -> str:
    """Return the full salted SHA-256 hex of the machine identity (cached)."""

    global _fingerprint_cache
    if _fingerprint_cache is None:
        if os.name != "nt":
            raise MachineFingerprintError("许可证功能仅支持 Windows 平台。")
        combined = f"{_HASH_SALT}|{_machine_guid()}|{_system_uuid()}"
        _fingerprint_cache = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return _fingerprint_cache


def machine_short_code() -> str:
    """Return the grouped machine code shown to users: ``XXXX-XXXX-…-XXXX``."""

    return _group(machine_fingerprint()[:_SHORT_CODE_LEN])


def normalize_short_code(code: str) -> str:
    """Normalize user/vendor input to the canonical grouped short code.

    Accepts dashed/undashed, any case, surrounding whitespace and ignores
    internal spaces. Raises ``ValueError`` when the content is not 24 hex
    characters.
    """

    compact = re.sub(r"[\s\-]+", "", code or "").upper()
    if len(compact) != _SHORT_CODE_LEN or not re.fullmatch(r"[0-9A-F]+", compact):
        raise ValueError("机器码必须是 24 位十六进制字符。")
    return _group(compact)


def is_valid_short_code(code: str) -> bool:
    """Return True when ``code`` normalizes to a canonical short code."""

    try:
        normalize_short_code(code)
    except ValueError:
        return False
    return True


def _group(compact: str) -> str:
    upper = compact.upper()
    return "-".join(upper[index : index + 4] for index in range(0, _SHORT_CODE_LEN, 4))


def _machine_guid() -> str:
    """Read ``MachineGuid`` from the Windows registry."""

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
    except OSError as error:
        raise MachineFingerprintError(f"无法读取 MachineGuid：{error}") from error
    guid = str(value).strip()
    if not guid:
        raise MachineFingerprintError("MachineGuid 为空。")
    return guid


def _system_uuid() -> str:
    """Read the system UUID from WMI ``Win32_ComputerSystemProduct``."""

    try:
        import win32com.client
    except ImportError as error:
        raise MachineFingerprintError("缺少 pywin32，无法读取系统 UUID。") from error
    try:
        wmi = win32com.client.GetObject("winmgmts:")
        products = wmi.ExecQuery("SELECT UUID FROM Win32_ComputerSystemProduct")
        for item in products:
            uuid = str(item.UUID).strip()
            if uuid:
                return uuid
    except Exception as error:  # pywintypes.com_error 等统一收口
        raise MachineFingerprintError(f"无法读取系统 UUID：{error}") from error
    raise MachineFingerprintError("WMI 未返回系统 UUID。")


def _reset_cache() -> None:
    """Clear the cached fingerprint. 仅用于测试注入后重置。"""

    global _fingerprint_cache
    _fingerprint_cache = None


__all__ = [
    "MachineFingerprintError",
    "is_valid_short_code",
    "machine_fingerprint",
    "machine_short_code",
    "normalize_short_code",
]
