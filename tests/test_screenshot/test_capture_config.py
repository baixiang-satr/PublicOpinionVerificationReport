"""整页长图相关 TaskConfig 配置的校验与环境变量解析。"""
from __future__ import annotations

import pytest

from src.config.settings import AppConfig, TaskConfig


def test_long_screenshot_config_defaults() -> None:
    config = TaskConfig()
    assert config.max_full_page_screenshot_height == 20_000
    assert config.long_screenshot_max_bytes == 1_000_000


def test_long_screenshot_max_bytes_validation() -> None:
    with pytest.raises(ValueError, match="long_screenshot_max_bytes"):
        TaskConfig(long_screenshot_max_bytes=99_999)
    with pytest.raises(ValueError, match="long_screenshot_max_bytes"):
        TaskConfig(long_screenshot_max_bytes=20_000_001)


def test_long_screenshot_config_from_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POR_LONG_SCREENSHOT_MAX_BYTES", "800000")
    monkeypatch.setenv("POR_MAX_FULL_PAGE_SCREENSHOT_HEIGHT", "15000")
    config = AppConfig.from_environment(tmp_path)
    assert config.task.long_screenshot_max_bytes == 800_000
    assert config.task.max_full_page_screenshot_height == 15_000
