"""Bridge 许可证守卫测试：未激活拦截、激活放行、bootstrap 携带授权信息。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.license.crypto import encode_code, generate_keypair
from src.license.manager import LicenseManager
from src.license.models import LicensePayload, LicenseStatus
from src.webui.bridge import WebUIBridge
from src.webui.runner import EventSink

_MACHINE = "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA"
_NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class _PassthroughProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return plaintext

    def unprotect(self, ciphertext: bytes) -> bytes:
        return ciphertext


def _config(tmp_path: Path) -> AppConfig:
    template = TemplateConfig(output_dir=tmp_path / "output")
    task = TaskConfig(auth_store_dir=tmp_path / "auth")
    return AppConfig(template=template, task=task)


@pytest.fixture()
def keypair() -> tuple[str, str]:
    return generate_keypair()


def _manager(tmp_path: Path, public_pem: str) -> LicenseManager:
    return LicenseManager(
        tmp_path / "license.dat",
        public_key_pem=public_pem,
        protector=_PassthroughProtector(),
        machine_code_provider=lambda: _MACHINE,
        now_provider=lambda: _NOW,
    )


def _bridge(tmp_path: Path, public_pem: str) -> WebUIBridge:
    return WebUIBridge(
        _config(tmp_path),
        EventSink(),
        license_manager=_manager(tmp_path, public_pem),
    )


def _issue(private_pem: str, *, machine: str = _MACHINE) -> str:
    payload = LicensePayload(
        license_id="L-TEST",
        licensee="测试客户",
        machine_id=machine,
        issued_at=_NOW - timedelta(days=1),
        expires_at=_NOW + timedelta(days=365),
    )
    return encode_code(payload, private_pem)


def test_bootstrap_carries_license_info(tmp_path: Path, keypair: tuple[str, str]) -> None:
    bridge = _bridge(tmp_path, keypair[1])
    boot = bridge.get_bootstrap()
    json.dumps(boot)  # js_api 载荷必须可 JSON 序列化
    license_info = boot["license"]
    assert license_info["activated"] is False
    assert license_info["status"] == LicenseStatus.NOT_ACTIVATED.value
    assert license_info["machine_code"] == _MACHINE
    assert license_info["ok"] is False


def test_guarded_operations_blocked_when_unactivated(
    tmp_path: Path, keypair: tuple[str, str]
) -> None:
    bridge = _bridge(tmp_path, keypair[1])
    calls = (
        lambda: bridge.start_crawl("x.txt"),
        lambda: bridge.retry_failed(),
        lambda: bridge.resume_checkpoint(True),
        lambda: bridge.export_zip(),
        lambda: bridge.pick_input_file(),
        lambda: bridge.pick_zip_file(),
        lambda: bridge.start_region_capture(1, "content"),
        lambda: bridge.auth_login("weibo"),
    )
    for call in calls:
        result = call()
        assert result["ok"] is False
        assert result["code"] == "LICENSE_REQUIRED"


def test_activation_unlocks_operations(tmp_path: Path, keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    bridge = _bridge(tmp_path, public_pem)
    result = bridge.license_activate(_issue(private_pem))
    assert result["ok"] is True
    assert result["activated"] is True
    assert result["licensee"] == "测试客户"
    # 守卫放行后进入原业务逻辑（空输入 → 业务层报错而非 LICENSE_REQUIRED）
    blocked = bridge.start_crawl("")
    assert blocked.get("code") != "LICENSE_REQUIRED"
    assert blocked["ok"] is False


def test_activate_rejects_tampered_code(tmp_path: Path, keypair: tuple[str, str]) -> None:
    bridge = _bridge(tmp_path, keypair[1])
    other_private, _ = generate_keypair()
    result = bridge.license_activate(_issue(other_private))
    assert result["ok"] is False
    assert result["status"] == LicenseStatus.BAD_SIGNATURE.value


def test_license_status_roundtrip(tmp_path: Path, keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    bridge = _bridge(tmp_path, public_pem)
    assert bridge.license_status()["activated"] is False
    bridge.license_activate(_issue(private_pem))
    status = bridge.license_status()
    assert status["activated"] is True
    assert status["status"] == LicenseStatus.VALID.value
    deactivated = bridge.license_deactivate()
    assert deactivated["activated"] is False
