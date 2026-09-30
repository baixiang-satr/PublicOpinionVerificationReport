"""B/S 服务入口：uvicorn 启动（默认 127.0.0.1:16667）+ 自动打开浏览器。

环境变量：
- ``POIR_HOST``：绑定地址，默认 127.0.0.1；服务器部署用 0.0.0.0；
- ``POIR_PORT``：端口，默认 16667（Chrome/Edge 封锁 6665-6669 区段，不可用 6667）；
- ``POIR_NO_BROWSER``：置 1 时启动后不自动打开浏览器。
"""
from __future__ import annotations

import os
from threading import Timer
import webbrowser

from src.config.settings import AppConfig
from src.services import recovery_mirror
from src.server.app import create_app
from src.server.events import WebEventSink, WebSocketHub
from src.webui.bridge import WebUIBridge

DEFAULT_PORT = 16667


def server_host_port() -> tuple[str, int]:
    host = os.environ.get("POIR_HOST", "").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("POIR_PORT", "").strip() or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return host, port


def run_server() -> int:
    try:
        import uvicorn
    except ImportError:
        print("缺少 FastAPI/Uvicorn，请先运行：pip install -r requirements.txt")
        return 2

    # 任务恢复镜像（断点/截图备份到 LOCALAPPDATA）：仅应用入口启用。
    recovery_mirror.enable()
    config = AppConfig.from_environment()
    hub = WebSocketHub()
    sink = WebEventSink(hub)
    bridge = WebUIBridge(config, sink)
    app = create_app(config=config, bridge=bridge, hub=hub)
    host, port = server_host_port()
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/"
    if os.environ.get("POIR_NO_BROWSER", "").strip().lower() not in {"1", "true", "yes"}:
        Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"网安见微·智舆 服务已启动：{url}（Ctrl+C 停止）")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        # 关闭常驻截图浏览器并把会话内刷新的登录态写回加密库。
        bridge.capture.shutdown()
    return 0


__all__ = ["DEFAULT_PORT", "run_server", "server_host_port"]
