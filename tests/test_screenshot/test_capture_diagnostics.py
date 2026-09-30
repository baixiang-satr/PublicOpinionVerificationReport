"""capture_diagnostics：环境收集、渲染几何探测与诊断落盘（全离线）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.config.settings import TaskConfig
from src.screenshot import capture_diagnostics


class GeometryPage:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def evaluate(self, _script: str, *_args: Any) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class NoEvaluatePage:
    pass


@pytest.mark.asyncio
async def test_probe_page_geometry_ok_when_matching() -> None:
    page = GeometryPage(
        {"innerWidth": 1440, "innerHeight": 900, "devicePixelRatio": 1.0}
    )

    result = await capture_diagnostics.probe_page_geometry(page, TaskConfig())

    assert result["ok"] is True
    assert result["warnings"] == []
    assert result["inner_width"] == 1440
    assert result["inner_height"] == 900
    assert result["device_pixel_ratio"] == 1.0


@pytest.mark.asyncio
async def test_probe_page_geometry_flags_viewport_and_dpr_mismatch() -> None:
    page = GeometryPage(
        {"innerWidth": 960, "innerHeight": 600, "devicePixelRatio": 1.5}
    )

    result = await capture_diagnostics.probe_page_geometry(page, TaskConfig())

    assert result["ok"] is False
    assert len(result["warnings"]) == 2
    assert "960x600" in result["warnings"][0]
    assert "1440x900" in result["warnings"][0]
    assert "devicePixelRatio" in result["warnings"][1]


@pytest.mark.asyncio
async def test_probe_page_geometry_tolerates_odd_pages() -> None:
    no_eval = await capture_diagnostics.probe_page_geometry(
        NoEvaluatePage(), TaskConfig()
    )
    assert no_eval["ok"] is False and no_eval["warnings"]

    non_dict = await capture_diagnostics.probe_page_geometry(
        GeometryPage(None), TaskConfig()
    )
    assert non_dict["ok"] is False and non_dict["warnings"]

    raising = await capture_diagnostics.probe_page_geometry(
        GeometryPage(RuntimeError("page closed")), TaskConfig()
    )
    assert raising["ok"] is False and raising["warnings"]


def test_collect_capture_environment_shape() -> None:
    env = capture_diagnostics.collect_capture_environment()

    assert env["os"]
    assert env["python"]
    assert env["dpi_awareness"]
    assert isinstance(env["monitors"], list)
    for monitor in env["monitors"]:
        assert monitor["width"] > 0 and monitor["height"] > 0


def test_persist_capture_environment_writes_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        capture_diagnostics,
        "collect_capture_environment",
        lambda: {"os": "TestOS", "monitors": []},
    )

    path = capture_diagnostics.persist_capture_environment(
        tmp_path,
        config=TaskConfig(),
        geometry={"ok": True},
        extra={"note": "unit-test"},
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["environment"] == {"os": "TestOS", "monitors": []}
    assert payload["viewport"]["width"] == 1440
    assert payload["viewport"]["height"] == 900
    assert payload["viewport"]["background_crawl_browser"] is True
    assert payload["geometry"] == {"ok": True}
    assert payload["extra"] == {"note": "unit-test"}
    assert payload["collected_at"]


def test_persist_capture_environment_never_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("no win32 here")

    monkeypatch.setattr(
        capture_diagnostics,
        "collect_capture_environment",
        _boom,
    )

    path = capture_diagnostics.persist_capture_environment(tmp_path)

    assert path.name == capture_diagnostics.CAPTURE_ENVIRONMENT_FILE
    assert not path.exists()
