from datetime import datetime
import json
from pathlib import Path

import pytest

from src.auth.models import AuthProbeResult, AuthStatus
from src.auth.store import AuthProfileStore


class ReverseProtector:
    """Deterministic non-plaintext protector for store contract tests."""

    _PREFIX = b"protected:"

    def protect(self, plaintext: bytes) -> bytes:
        return self._PREFIX + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(self._PREFIX):
            raise ValueError("invalid protected payload")
        return ciphertext[len(self._PREFIX) :][::-1]


def _result(status: AuthStatus) -> AuthProbeResult:
    return AuthProbeResult(
        platform_key="zhihu",
        status=status,
        checked_at=datetime.now().astimezone(),
        original_url="https://www.zhihu.com/question/362425387",
        message="validated",
    )


def test_validated_state_is_encrypted_and_index_contains_only_masked_phone(
    tmp_path: Path,
) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    secret = "super-secret-session-token"
    state = {
        "cookies": [
            {
                "name": "session",
                "value": secret,
                "domain": ".zhihu.com",
                "path": "/",
            }
        ],
        "origins": [],
    }

    profile = store.commit_validated_state(
        "zhihu",
        state,
        _result(AuthStatus.VALID),
        phone="13800138000",
    )

    index_text = store.index_path.read_text(encoding="utf-8")
    state_bytes = (store.states_dir / profile.state_filename).read_bytes()
    index = json.loads(index_text)
    assert secret not in index_text
    assert "13800138000" not in index_text
    assert index["profiles"]["zhihu"]["masked_phone"] == "138****8000"
    assert secret.encode() not in state_bytes
    assert store.load_state("zhihu") == state
    assert store.has_valid_state("zhihu")


def test_unvalidated_state_cannot_be_committed(tmp_path: Path) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())

    with pytest.raises(ValueError, match="successfully validated"):
        store.commit_validated_state(
            "zhihu",
            {"cookies": [], "origins": []},
            _result(AuthStatus.AUTH_REQUIRED),
        )

    assert not store.index_path.exists()


def test_delete_removes_only_selected_platform_state(tmp_path: Path) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    state = {"cookies": [], "origins": []}
    profile = store.commit_validated_state(
        "zhihu",
        state,
        _result(AuthStatus.VALID),
    )
    state_path = store.states_dir / profile.state_filename

    reset = store.delete_state("zhihu")

    assert not state_path.exists()
    assert reset.status == AuthStatus.UNKNOWN
    assert reset.state_filename is None
    assert not store.has_valid_state("zhihu")


def test_result_index_does_not_persist_arbitrary_content_url(
    tmp_path: Path,
) -> None:
    store = AuthProfileStore(tmp_path / "auth", protector=ReverseProtector())
    private_url = "https://www.zhihu.com/question/123?private_token=do-not-store"
    store.record_result(
        AuthProbeResult(
            platform_key="zhihu",
            status=AuthStatus.AUTH_REQUIRED,
            checked_at=datetime.now().astimezone(),
            original_url=private_url,
            barrier_code="LOGIN_REQUIRED",
            message="login required",
        )
    )

    index_text = store.index_path.read_text(encoding="utf-8")
    assert private_url not in index_text
    assert "private_token" not in index_text
