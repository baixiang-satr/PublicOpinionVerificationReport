"""窗口关闭确认：点关闭时否决默认关闭，交前端询问「直接退出 / 最小化到任务栏」。

pywebview 的 ``window.events.closing`` 处理器返回 ``False`` 即取消关闭；
这里同时经 EventSink 通知前端弹三态确认框，用户选择后再由
``ExitControlMixin`` 的 js_api 方法执行真正的退出或最小化。最小化仅做
任务栏最小化（不引入系统托盘依赖），后台抓取线程与事件推送不受影响。

两个 Windows（WinForms + WebView2）后端的实测约束：

1. ``events.closing`` 以 ``should_lock=True`` 创建，处理器在 GUI 线程上
   **同步**执行。``EventSink.emit`` 底层的 ``window.evaluate_js`` 会阻塞
   当前线程等待 JS 回调，而该回调又必须回到 GUI 线程派发——在处理器里
   同步 emit 会让 GUI 线程自我等待，整个窗口立刻假死（表现为确认弹窗
   已弹出，但点「最小化到任务栏」毫无反应）。因此事件推送必须放后台线程。
2. 处理器返回 False 会否决**一切** ``Close()``，包括 ``window.destroy()``
   内部那一次。用户确认退出后必须先置允许关闭标志，否则退出会被自己的
   否决逻辑拦下。
"""

from __future__ import annotations

from threading import Thread
from typing import Any

_ALLOW_CLOSE_ATTR = "_poir_allow_close"


def install_close_confirmation(window: Any, sink: Any) -> None:
    """订阅窗口关闭事件：后台线程通知前端弹确认框，并否决默认关闭。"""

    def _on_closing() -> bool:
        # 用户已确认退出（confirm_exit 置标志后 destroy）：放行。
        if getattr(window, _ALLOW_CLOSE_ATTR, False):
            return True
        # 严禁在此同步 sink.emit：closing 处理器跑在 GUI 线程上，
        # evaluate_js 阻塞等待的 JS 回调需回到 GUI 线程派发，同步调用
        # 会立刻死锁整个窗口，必须放后台线程推送。
        Thread(target=sink.emit, args=("app_closing", {}), daemon=True).start()
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
        """用户选择「直接退出」：放行关闭事件后 destroy 走正常 shutdown 清理流程。"""

        window = self._exit_window()
        # destroy() 内部也是一次 Close()，会被 closing 否决逻辑拦下；
        # 先置允许关闭标志，让本次关闭放行。
        setattr(window, _ALLOW_CLOSE_ATTR, True)
        window.destroy()
        return {"ok": True}

    def minimize_window(self) -> dict:
        """用户选择「最小化到任务栏」：窗口收起，后台任务继续运行。"""

        self._exit_window().minimize()
        return {"ok": True}


__all__ = ["ExitControlMixin", "install_close_confirmation"]
