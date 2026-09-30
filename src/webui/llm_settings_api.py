"""大模型设置 js_api mixin：读取 / 保存 / 连通性测试。

宿主类需提供：``self._sink``（EventSink）。连通性测试在后台线程执行，
结果经 ``llm_test`` 事件推给前端，避免阻塞 HTTP 请求线程。
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from src.auth.protection import StateProtectionError
from src.llm.client import LlmClient
from src.llm.models import DEFAULT_BASE_URL, LlmSettings
from src.llm.store import default_llm_settings_store

_TEST_SYSTEM_PROMPT = "你是连通性测试助手。"
_TEST_USER_PROMPT = "请只回复两个字：正常"


class LlmSettingsApiMixin:
    _sink: Any = None

    def get_llm_settings(self) -> dict:
        """读取大模型设置（Key 只回传掩码，不回传原文）。"""

        settings = default_llm_settings_store().load()
        return {"ok": True, "settings": settings.masked_payload()}

    def save_llm_settings(self, payload: dict) -> dict:
        """保存大模型设置；api_key 留空表示保留已保存的 Key。"""

        store = default_llm_settings_store()
        current = store.load()
        try:
            enabled = bool(payload.get("enabled"))
            base_url = str(payload.get("base_url") or "").strip() or DEFAULT_BASE_URL
            model = str(payload.get("model") or "").strip()
            timeout = float(payload.get("timeout_seconds") or current.timeout_seconds)
            max_chars = int(payload.get("max_input_chars") or current.max_input_chars)
            api_key = str(payload.get("api_key") or "").strip()
        except (TypeError, ValueError) as error:
            return {"ok": False, "message": f"参数无效：{error}"}
        if not base_url.startswith(("http://", "https://")):
            return {"ok": False, "message": "接口地址必须以 http(s):// 开头。"}
        if enabled and not model:
            return {"ok": False, "message": "启用大模型前请填写模型名称。"}
        if not 5.0 <= timeout <= 120.0:
            return {"ok": False, "message": "超时时间需在 5-120 秒之间。"}
        if not 500 <= max_chars <= 20000:
            return {"ok": False, "message": "输入截断长度需在 500-20000 字之间。"}
        settings = LlmSettings(
            enabled=enabled,
            base_url=base_url,
            model=model,
            api_key=api_key or current.api_key,
            timeout_seconds=timeout,
            max_input_chars=max_chars,
        )
        try:
            store.save(settings)
        except (StateProtectionError, OSError) as error:
            return {"ok": False, "message": f"保存失败：{error}"}
        return {"ok": True, "settings": settings.masked_payload()}

    def test_llm_connection(self) -> dict:
        """后台线程执行连通性测试；结果经 llm_test 事件回推。"""

        settings = default_llm_settings_store().load()
        if not settings.is_complete():
            return {
                "ok": False,
                "message": "请先保存完整的大模型设置（启用 + 接口地址 + 模型 + API Key）。",
            }
        sink = self._sink
        if sink is None:
            return {"ok": False, "message": "事件通道不可用。"}
        threading.Thread(
            target=_run_connection_test,
            args=(settings, sink),
            daemon=True,
        ).start()
        return {"ok": True, "message": "正在测试连接…"}


def _run_connection_test(settings: LlmSettings, sink: Any) -> None:
    started = time.monotonic()

    def _payload(ok: bool, message: str, reply: str = "") -> dict:
        return {
            "ok": ok,
            "message": message,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": reply,
        }

    try:
        reply = asyncio.run(
            LlmClient(settings).complete(_TEST_SYSTEM_PROMPT, _TEST_USER_PROMPT)
        )
    except Exception as error:  # noqa: BLE001 - 失败原因原样回显给用户
        sink.emit("llm_test", _payload(False, f"连接失败：{error}"))
        return
    sink.emit("llm_test", _payload(True, "连接成功，模型响应正常。", reply[:40]))
