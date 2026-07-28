import os

import pytest

from src.auth.protection import WindowsDpapiProtector


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is Windows-only")
def test_windows_dpapi_round_trip_is_not_plaintext() -> None:
    protector = WindowsDpapiProtector()
    plaintext = b'{"cookies":[{"value":"test-session-secret"}]}'

    ciphertext = protector.protect(plaintext)

    assert plaintext not in ciphertext
    assert protector.unprotect(ciphertext) == plaintext
