"""
pytest 共享配置 — 测试夹具和全局配置
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _llm_settings_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """隔离真实 LLM 设置文件：全部测试默认读到空配置（大模型兜底关闭）。

    防止本机已保存的设置让测试意外发起真实 API 请求；LLM 相关测试在
    用例内自行注入 store/client。
    """

    import src.llm.store as llm_store_module

    monkeypatch.setattr(
        llm_store_module,
        "_default_store",
        llm_store_module.LlmSettingsStore(tmp_path / "llm_settings.json"),
    )
