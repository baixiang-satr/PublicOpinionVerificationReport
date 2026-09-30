"""业务桥应用包（B/S 下经 FastAPI REST/WS 暴露给 Vue 前端）。

前端构建产物在 ``web/dist``，由 ``src/server`` 静态托管；Python 侧通过
:class:`WebUIBridge` 暴露全部业务能力，事件经 WebSocket 推送。
"""
