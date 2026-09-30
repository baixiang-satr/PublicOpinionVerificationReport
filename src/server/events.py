"""B/S 事件通道：WebSocket 广播中心 + EventSink 适配器。

C/S 时代事件经 ``EventSink.emit`` → ``window.evaluate_js`` 推给内嵌页面；
B/S 下改为 ``WS /ws/events`` 广播。emit 来自抓取/登录等后台线程，而 WS 发送
属于 uvicorn 事件循环，因此经 ``loop.call_soon_threadsafe`` 交接，发射线程
绝不阻塞等待网络 I/O。
"""

from __future__ import annotations

import asyncio
import json
import logging
from threading import Lock
from typing import Any

from src.webui.runner import EventSink

logger = logging.getLogger(__name__)


class WebSocketHub:
    """管理 /ws/events 连接并向全部客户端广播桥事件。"""

    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """服务启动后绑定 uvicorn 事件循环（启动前的事件直接丢弃）。"""

        with self._lock:
            self._loop = loop

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        with self._lock:
            self._connections.add(websocket)

    def disconnect(self, websocket: Any) -> None:
        with self._lock:
            self._connections.discard(websocket)

    def broadcast(self, event_type: str, payload: dict | None = None) -> None:
        """线程安全的广播入口：可被任意后台线程调用。"""

        data = json.dumps(
            {"type": event_type, "payload": payload or {}},
            ensure_ascii=False,
        )
        with self._lock:
            loop = self._loop
            connections = tuple(self._connections)
        if loop is None or not connections:
            return

        def _schedule() -> None:
            asyncio.ensure_future(self._send_all(data))

        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError:  # 循环已关闭（服务停止中）：事件丢弃
            pass

    async def _send_all(self, data: str) -> None:
        with self._lock:
            connections = tuple(self._connections)
        dead: list[Any] = []
        for websocket in connections:
            try:
                await websocket.send_text(data)
            except Exception:  # noqa: BLE001 — 单个断连不影响其他客户端
                dead.append(websocket)
        if dead:
            with self._lock:
                for websocket in dead:
                    self._connections.discard(websocket)


class WebEventSink(EventSink):
    """EventSink 的 B/S 实现：emit 转发到 WebSocketHub 广播。"""

    def __init__(self, hub: WebSocketHub) -> None:
        super().__init__()
        self._hub = hub

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        self._hub.broadcast(event_type, payload)


__all__ = ["WebEventSink", "WebSocketHub"]
