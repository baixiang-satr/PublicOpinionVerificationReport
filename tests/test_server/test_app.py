"""B/S 服务端测试：REST 桥、上传/下载、许可证守卫、WS 事件广播（全离线）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config.settings import AppConfig, TaskConfig, TemplateConfig
from src.license.models import LicenseInfo, LicenseStatus
import src.webui.bridge as bridge_module
from src.server.app import create_app
from src.server.events import WebEventSink, WebSocketHub
from src.webui.bridge import WebUIBridge


class _AlwaysValidLicense:
    """测试替身：永远视为已激活。"""

    def status(self) -> LicenseInfo:
        return LicenseInfo(
            activated=True,
            status=LicenseStatus.VALID,
            message="",
            machine_code="TEST",
        )


class _NeverValidLicense:
    """测试替身：永远未激活（验证守卫透传）。"""

    def status(self) -> LicenseInfo:
        return LicenseInfo(
            activated=False,
            status=LicenseStatus.NOT_ACTIVATED,
            message="尚未激活。",
            machine_code="TEST",
        )


def _config(tmp_path: Path) -> AppConfig:
    template = TemplateConfig(output_dir=tmp_path / "output")
    task = TaskConfig(auth_store_dir=tmp_path / "auth")
    return AppConfig(template=template, task=task)


def _client(tmp_path: Path, *, activated: bool = True) -> tuple[TestClient, WebSocketHub, WebUIBridge]:
    manager = _AlwaysValidLicense if activated else _NeverValidLicense
    config = _config(tmp_path)
    hub = WebSocketHub()
    bridge = WebUIBridge(config, WebEventSink(hub), license_manager=manager())
    app = create_app(config=config, bridge=bridge, hub=hub, dist_dir=tmp_path / "dist")
    return TestClient(app), hub, bridge


def test_rest_bridge_roundtrip(tmp_path: Path) -> None:
    client, _hub, _bridge = _client(tmp_path)
    with client:
        response = client.post("/api/get_bootstrap", json={"args": []})
        assert response.status_code == 200
        boot = response.json()
        assert boot["license"]["activated"] is True
        assert boot["options"]["max_concurrency"] >= 1

        response = client.post(
            "/api/set_options",
            json={"args": [{
                "max_concurrency": 4,
                "page_timeout_seconds": 50,
                "max_retries": 1,
                "screenshot_format": "png",
            }]},
        )
        assert response.json() == {"ok": True}

        assert client.post("/api/no_such_method", json={"args": []}).status_code == 404
        assert client.post("/api/letter_state", json={"args": []}).json() == {"names": []}


def test_license_guard_passthrough(tmp_path: Path) -> None:
    client, _hub, _bridge = _client(tmp_path, activated=False)
    with client:
        response = client.post("/api/start_crawl", json={"args": ["x.txt"]})
        body = response.json()
        assert body["ok"] is False
        assert body["code"] == "LICENSE_REQUIRED"
        # 下载端点同样被守卫拦截
        blocked = client.get("/api/download/job-zip")
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "LICENSE_REQUIRED"


def test_upload_input_file(tmp_path: Path) -> None:
    client, _hub, _bridge = _client(tmp_path)
    with client:
        content = b"https://example.com/a\nhttps://example.com/b\nnot-a-url\n"
        response = client.post(
            "/api/upload/input",
            files={"file": ("urls.txt", content, "text/plain")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["url_count"] == 2
        assert body["rejected_count"] == 1
        # 暂存文件保留（任务运行期间仍要引用）
        assert (tmp_path / "output" / "_uploads").is_dir()


def test_upload_input_blocked_when_unactivated(tmp_path: Path) -> None:
    client, _hub, _bridge = _client(tmp_path, activated=False)
    with client:
        response = client.post(
            "/api/upload/input",
            files={"file": ("urls.txt", b"https://example.com/a\n", "text/plain")},
        )
        assert response.json()["code"] == "LICENSE_REQUIRED"


def test_upload_auth_state_import(tmp_path: Path) -> None:
    client, _hub, bridge = _client(tmp_path)
    with client:
        state = {
            "cookies": [
                {"name": "SUB", "value": "x", "domain": ".weibo.com", "path": "/"},
            ],
            "origins": [],
        }
        response = client.post(
            "/api/upload/auth-state",
            params={"platform": "weibo"},
            files={"file": ("state.json", json.dumps(state).encode(), "application/json")},
        )
        body = response.json()
        assert body["ok"] is True
        stored = bridge.auth.store().load_state("weibo")
        assert stored is not None
        assert bridge.auth.store().profile_for("weibo").status.value == "valid"

        guest = {"cookies": [{"name": "_ga", "value": "1"}], "origins": []}
        rejected = client.post(
            "/api/upload/auth-state",
            params={"platform": "douyin"},
            files={"file": ("state.json", json.dumps(guest).encode(), "application/json")},
        )
        assert rejected.json()["ok"] is False

        bad = client.post(
            "/api/upload/auth-state",
            params={"platform": "weibo"},
            files={"file": ("state.json", b"not json", "application/json")},
        )
        assert bad.json()["ok"] is False


def test_download_job_zip(tmp_path: Path) -> None:
    client, _hub, bridge = _client(tmp_path)
    with client:
        assert client.get("/api/download/job-zip").status_code == 404
        deliver = tmp_path / "output" / "job-x"
        deliver.mkdir(parents=True)
        archive = deliver / "template.zip"
        archive.write_bytes(b"PK\x05\x06" + b"0" * 22)
        bridge.jobs.last_deliver_dir = deliver
        response = client.get("/api/download/job-zip")
        assert response.status_code == 200
        assert response.content == archive.read_bytes()


def test_download_manual_entries_without_session(tmp_path: Path) -> None:
    client, _hub, _bridge = _client(tmp_path)
    with client:
        response = client.get("/api/download/manual-entries")
        assert response.status_code == 404
        assert response.json()["ok"] is False


def test_ws_events_broadcast(tmp_path: Path) -> None:
    client, hub, _bridge = _client(tmp_path)
    with client:
        with client.websocket_connect("/ws/events") as ws:
            hub.broadcast("log", {"message": "你好", "level": "INFO"})
            event = ws.receive_json()
            assert event == {"type": "log", "payload": {"message": "你好", "level": "INFO"}}


def test_static_frontend_mounted(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>poir</html>", encoding="utf-8")
    client, _hub, _bridge = _client(tmp_path)
    client.app.dependency_overrides = {}
    with client:
        # _client 默认 dist_dir=tmp/dist（此处已写入 index.html）
        response = client.get("/")
        assert response.status_code == 200
        assert "poir" in response.text
