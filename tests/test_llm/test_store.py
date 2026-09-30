"""LlmSettingsStore 持久化测试（fake protector，全离线）。"""

from pathlib import Path

from src.llm.models import LlmSettings
from src.llm.store import LlmSettingsStore


class _XorProtector:
    """可逆假加密：protect/unprotect 往返一致。"""

    def protect(self, plaintext: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in ciphertext)


def _store(tmp_path: Path) -> LlmSettingsStore:
    return LlmSettingsStore(tmp_path / "llm_settings.json", protector=_XorProtector())


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    settings = _store(tmp_path).load()
    assert settings.enabled is False
    assert settings.is_complete() is False


def test_save_load_roundtrip_and_key_encrypted_at_rest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = LlmSettings(
        enabled=True,
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="sk-secret-123456",
        timeout_seconds=45.0,
        max_input_chars=8000,
    )
    store.save(saved)

    assert store.load() == saved
    raw = (tmp_path / "llm_settings.json").read_text(encoding="utf-8")
    assert "sk-secret-123456" not in raw
    assert "api_key_b64" in raw


def test_corrupt_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "llm_settings.json"
    path.write_text("{ not json", encoding="utf-8")
    assert _store(tmp_path).load().enabled is False


def test_undecryptable_key_is_cleared(tmp_path: Path) -> None:
    path = tmp_path / "llm_settings.json"
    path.write_text(
        '{"enabled": true, "base_url": "https://x", "model": "m",'
        ' "api_key_b64": "!!!not-base64!!!"}',
        encoding="utf-8",
    )
    loaded = _store(tmp_path).load()
    assert loaded.api_key == ""
    assert loaded.is_complete() is False


def test_masked_payload_never_contains_key() -> None:
    settings = LlmSettings(
        enabled=True,
        base_url="https://x",
        model="m",
        api_key="sk-abcdef123456789",
    )
    payload = settings.masked_payload()
    assert payload["has_api_key"] is True
    assert payload["api_key_masked"] == "sk-a…6789"
    assert "sk-abcdef123456789" not in str(payload)
