"""桌面应用入口：pywebview 窗口 + Vue 前端 + WebUIBridge。"""
from __future__ import annotations

import os
from pathlib import Path

from src.config.settings import AppConfig, PROJECT_ROOT
from src.webui.bridge import WebUIBridge
from src.webui.runner import EventSink

_DIST_INDEX = PROJECT_ROOT / "web" / "dist" / "index.html"


def run_app() -> int:
    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先运行：pip install -r requirements.txt")
        return 2

    config = AppConfig.from_environment()
    sink = EventSink()
    window_box: dict[str, object] = {}
    bridge = WebUIBridge(config, sink, window_provider=lambda: window_box["window"])

    dev_url = os.environ.get("POIR_WEB_DEV_URL", "").strip()
    if dev_url:
        url = dev_url
    elif _DIST_INDEX.is_file():
        url = str(_DIST_INDEX)
    else:
        print("缺少前端构建产物 web/dist/index.html，请先在 web/ 目录运行 npm run build。")
        return 2

    window = webview.create_window(
        "舆情验证报告工作台",
        url,
        js_api=bridge,
        width=1280,
        height=860,
        min_size=(360, 640),
        text_select=True,
    )
    window_box["window"] = window
    sink.bind(window)
    # http_server=True：内置 HTTP 服务加载 dist，规避 file:// 下
    # ES module / 动态 import 的 CORS 限制（WebView2 会拦截）。
    webview.start(debug=bool(dev_url), http_server=True)
    return 0


__all__ = ["run_app"]
