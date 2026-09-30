"""LlmSettingsApiMixin 测试（tmp 存储 + fake sink/client，全离线）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import src.llm.store as llm_store_module
import src.webui.llm_settings_api as llm_api_module
from src.webui.llm_settings_api import LlmSettingsApiMixin


class _XorProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in ciphertext)


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


class _Host(LlmSettingsApiMixin):
    pass


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[_Host, _Sink, Path]:
    sink = _Sink()
    store = llm_store_module.LlmSettingsStore(
        tmp_path / "llm_settings.json",
        protector=_XorProtector(),
    )
    monkeypatch.setattr(llm_store_module, "_default_store", store)
    instance = _Host()
    instance._sink = sink
    return instance, sink, tmp_path / "llm_settings.json"


def test_get_llm_settings_defaults(host) -> None:
    instance, _, _ = host
    result = instance.get_llm_settings()
    assert result["ok"] is True
    assert result["settings"]["enabled"] is False
    assert result["settings"]["has_api_key"] is False


def test_save_and_reload_settings(host) -> None:
    instance, _, path = host
    result = instance.save_llm_settings(
        {
            "enabled": True,
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-secret-999",
            "timeout_seconds": 45,
            "max_input_chars": 8000,
        }
    )
    assert result["ok"] is True
    assert result["settings"]["has_api_key"] is True
    assert "sk-secret-999" not in path.read_text(encoding="utf-8")

    reloaded = instance.get_llm_settings()["settings"]
    assert reloaded["enabled"] is True
    assert reloaded["model"] == "deepseek-chat"
    assert reloaded["api_key_masked"] == "sk-s…-999"


def test_save_with_empty_key_keeps_existing(host) -> None:
    instance, _, _ = host
    payload = {
        "enabled": True,
        "base_url": "https://x.test/v1",
        "model": "m",
        "api_key": "sk-keep-me-123",
        "timeout_seconds": 30,
        "max_input_chars": 6000,
    }
    assert instance.save_llm_settings(payload)["ok"] is True
    assert instance.save_llm_settings({**payload, "api_key": ""})["ok"] is True
    assert instance.get_llm_settings()["settings"]["api_key_masked"] == "sk-k…-123"


def test_save_rejects_invalid_payload(host) -> None:
    instance, _, _ = host
    bad_url = instance.save_llm_settings({"enabled": False, "base_url": "ftp://x"})
    assert bad_url["ok"] is False
    no_model = instance.save_llm_settings(
        {"enabled": True, "base_url": "https://x.test", "model": ""}
    )
    assert no_model["ok"] is False


def test_test_connection_requires_complete_settings(host) -> None:
    instance, sink, _ = host
    result = instance.test_llm_connection()
    assert result["ok"] is False
    assert sink.events == []


def test_test_connection_success_emits_event(host, monkeypatch: pytest.MonkeyPatch) -> None:
    instance, sink, _ = host
    instance.save_llm_settings(
        {
            "enabled": True,
            "base_url": "https://x.test/v1",
            "model": "m",
            "api_key": "sk-x",
            "timeout_seconds": 30,
            "max_input_chars": 6000,
        }
    )

    class _OkClient:
        def __init__(self, _settings) -> None:
            pass

        async def complete(self, _system: str, _user: str) -> str:
            return "正常"

    monkeypatch.setattr(llm_api_module, "LlmClient", _OkClient)
    assert instance.test_llm_connection()["ok"] is True
    event, payload = _wait_event(sink)
    assert event == "llm_test"
    assert payload["ok"] is True
    assert payload["reply"] == "正常"
    assert payload["latency_ms"] >= 0


def test_test_connection_failure_emits_error(host, monkeypatch: pytest.MonkeyPatch) -> None:
    instance, sink, _ = host
    instance.save_llm_settings(
        {
            "enabled": True,
            "base_url": "https://x.test/v1",
            "model": "m",
            "api_key": "sk-x",
            "timeout_seconds": 30,
            "max_input_chars": 6000,
        }
    )

    class _FailClient:
        def __init__(self, _settings) -> None:
            pass

        async def complete(self, _system: str, _user: str) -> str:
            raise RuntimeError("连接被拒绝")

    monkeypatch.setattr(llm_api_module, "LlmClient", _FailClient)
    assert instance.test_llm_connection()["ok"] is True
    event, payload = _wait_event(sink)
    assert event == "llm_test"
    assert payload["ok"] is False
    assert "连接被拒绝" in payload["message"]


def _wait_event(sink: _Sink, timeout: float = 5.0) -> tuple[str, dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sink.events:
            return sink.events[0]
        time.sleep(0.02)
    raise AssertionError("未在限时内收到 llm_test 事件")
