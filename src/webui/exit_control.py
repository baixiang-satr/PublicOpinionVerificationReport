"""窗口关闭确认：点关闭时否决默认关闭，交前端询问「直接退出 / 最小化到任务栏」。

pywebview 的 ``window.events.closing`` 处理器返回 ``False`` 即取消关闭；
这里同时经 EventSink 通知前端弹三态确认框，用户选择后再由
``ExitControlMixin`` 的 js_api 方法执行真正的退出或最小化。最小化仅做
任务栏最小化（不引入系统托盘依赖），后台抓取线程与事件推送不受影响。
"""

from __future__ import annotations

from typing import Any


def install_close_confirmation(window: Any, sink: Any) -> None:
    """订阅窗口关闭事件：否决默认关闭并通知前端弹确认框。"""

    def _on_closing() -> bool:
        sink.emit("app_closing", {})
        return False

    window.events.closing += _on_closing


class ExitControlMixin:
    """关闭确认后的两个 js_api 动作；宿主类需提供 ``_window_provider``。"""

    _window_provider: Any = None

    def _exit_window(self) -> Any:
        if self._window_provider is not None:
            return self._window_provider()
        import webview

        return webview.windows[0]

    def confirm_exit(self) -> dict:
        """用户选择「直接退出」：destroy 走正常 shutdown 清理流程。"""

        self._exit_window().destroy()
        return {"ok": True}

    def minimize_window(self) -> dict:
        """用户选择「最小化到任务栏」：窗口收起，后台任务继续运行。"""

        self._exit_window().minimize()
        return {"ok": True}


__all__ = ["ExitControlMixin", "install_close_confirmation"]
