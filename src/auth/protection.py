"""Windows current-user protection for authentication state blobs."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Protocol


class StateProtectionError(RuntimeError):
    """Raised when authentication state cannot be protected or restored."""


class StateProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class WindowsDpapiProtector:
    """Protect bytes for the current Windows user with DPAPI."""

    _DESCRIPTION = "PublicOpinionVerificationReport authentication state"
    _ENTROPY = b"PublicOpinionVerificationReport/auth-state/v1"
    _UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise StateProtectionError("Windows DPAPI is only available on Windows.")
        self._crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._configure_functions()

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, protect=False)

    def _configure_functions(self) -> None:
        blob_pointer = ctypes.POINTER(_DataBlob)
        self._crypt32.CryptProtectData.argtypes = (
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            blob_pointer,
        )
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = (
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            blob_pointer,
        )
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
        self._kernel32.LocalFree.restype = wintypes.HLOCAL

    def _transform(self, payload: bytes, *, protect: bool) -> bytes:
        input_blob, input_buffer = _blob_from_bytes(payload)
        entropy_blob, entropy_buffer = _blob_from_bytes(self._ENTROPY)
        output_blob = _DataBlob()
        description = wintypes.LPWSTR()
        if protect:
            succeeded = self._crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                self._DESCRIPTION,
                ctypes.byref(entropy_blob),
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        else:
            succeeded = self._crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                ctypes.byref(description),
                ctypes.byref(entropy_blob),
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        # Keep ctypes buffers alive until the native call has returned.
        _ = input_buffer, entropy_buffer
        if not succeeded:
            code = ctypes.get_last_error()
            raise StateProtectionError(f"Windows DPAPI operation failed with error {code}.")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                self._kernel32.LocalFree(output_blob.pbData)
            if description:
                self._kernel32.LocalFree(description)


class _DataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    )


def _blob_from_bytes(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data or b"\0")
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def default_state_protector() -> StateProtector:
    return WindowsDpapiProtector()
