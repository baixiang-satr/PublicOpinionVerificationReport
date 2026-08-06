"""Native, page-independent toolbar for interactive evidence capture."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import gc
from queue import Empty, SimpleQueue
from threading import Event, Thread, current_thread
from typing import Any


class NativeCaptureToolbar:
    """A tiny always-on-top Windows control that survives every navigation.

    Tk owns its own thread and message loop.  Button callbacks are marshalled
    back to the Playwright asyncio loop; all toolbar mutations are marshalled
    in the other direction through ``_commands``.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_action: Callable[[dict[str, str]], None],
        *,
        evidence_id: int,
        target: str,
    ) -> None:
        self._loop = loop
        self._on_action = on_action
        self._evidence_id = evidence_id
        self._target = target
        self._commands: SimpleQueue[tuple[str, str | None]] = SimpleQueue()
        self._ready = Event()
        self._closed = Event()
        # 仅保存字符串消息，绝不跨线程持有异常对象（traceback 会引用 Tk 帧）。
        self._error: str | None = None
        self._thread = Thread(
            target=self._run,
            daemon=True,
            name=f"poir-capture-toolbar-{evidence_id}",
        )

    def start(self, timeout: float = 5.0) -> None:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("截图控制条启动超时。")
        if self._error is not None:
            raise RuntimeError(f"无法打开页面外截图控制条：{self._error}")

    def hide(self) -> None:
        self._commands.put(("hide", None))

    def show(self, message: str | None = None) -> None:
        self._commands.put(("show", message))

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._commands.put(("close", None))
        if self._thread is not current_thread():
            self._thread.join(timeout=2.0)

    def _emit(self, action: str) -> None:
        try:
            self._loop.call_soon_threadsafe(
                self._on_action,
                {"action": action},
            )
        except RuntimeError:
            pass

    def _run(self) -> None:
        try:
            self._run_tk()
        except BaseException as error:  # noqa: BLE001 - forwarded to caller
            # 只保留字符串：异常 traceback 会引用 _run_tk 帧中的 root，一旦在
            # 其他线程被 GC 回收，Tcl 解释器会被错误线程销毁（Tcl_AsyncDelete
            # 致命崩溃）。except 结束即释放 error，traceback 随之断开。
            self._error = f"{type(error).__name__}: {error}"
            self._ready.set()
        finally:
            # Tcl 解释器由创建它的线程独占。_run_tk 返回/抛错后其帧已释放，
            # root 与控件/闭包组成的引用环变成不可达垃圾；必须在本线程就地
            # 回收，否则其他线程的 GC 会代为释放并触发 Tcl_AsyncDelete
            # （async handler deleted by the wrong thread）进程级崩溃。
            gc.collect()
            self._closed.set()

    def _run_tk(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        try:
            label = "正文" if self._target == "content" else "个人主页"
            default_text = (
                f"截图 #{self._evidence_id:03d} · {label}｜"
                "可切换到任意网页后再框选"
            )
            root.title("关联截图")
            root.attributes("-topmost", True)
            root.resizable(False, False)
            root.protocol("WM_DELETE_WINDOW", lambda: self._emit("cancel"))

            frame = ttk.Frame(root, padding=(12, 9))
            frame.grid(row=0, column=0, sticky="nsew")
            status = ttk.Label(frame, text=default_text)
            status.grid(row=0, column=0, columnspan=2, padx=(0, 4), pady=(0, 7))
            start = ttk.Button(frame, text="开始框选")
            cancel = ttk.Button(frame, text="取消截图")
            start.grid(row=1, column=0, sticky="ew", padx=(0, 6))
            cancel.grid(row=1, column=1, sticky="ew")

            def arm() -> None:
                start.state(["disabled"])
                cancel.state(["disabled"])
                status.configure(text="正在冻结当前屏幕…")
                root.withdraw()
                self._emit("arm")

            def quit_capture() -> None:
                start.state(["disabled"])
                cancel.state(["disabled"])
                status.configure(text="正在关闭…")
                self._emit("cancel")

            start.configure(command=arm)
            cancel.configure(command=quit_capture)
            root.update_idletasks()
            width = max(390, root.winfo_reqwidth())
            height = root.winfo_reqheight()
            x = max(0, root.winfo_screenwidth() - width - 24)
            root.geometry(f"{width}x{height}+{x}+24")

            def drain_commands() -> None:
                try:
                    while True:
                        command, message = self._commands.get_nowait()
                        if command == "hide":
                            root.withdraw()
                        elif command == "show":
                            status.configure(text=message or default_text)
                            start.state(["!disabled"])
                            cancel.state(["!disabled"])
                            root.deiconify()
                            root.attributes("-topmost", True)
                        elif command == "close":
                            root.destroy()
                            return
                except Empty:
                    pass
                root.after(40, drain_commands)

            root.after(40, drain_commands)
            self._ready.set()
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001 - 销毁兜底，不能再抛出
                pass
            # tkinter 模块级 _default_root 会持有 root，使引用环逃逸到其他
            # 线程的 GC；必须在本线程断开。
            if getattr(tk, "_default_root", None) is root:
                tk._default_root = None


def open_capture_toolbar(
    factory: Callable[..., Any],
    loop: asyncio.AbstractEventLoop,
    on_action: Callable[[dict[str, str]], None],
    *,
    evidence_id: int,
    target: str,
) -> Any:
    """Construct and synchronously wait for a native control to be usable."""

    toolbar = factory(
        loop,
        on_action,
        evidence_id=evidence_id,
        target=target,
    )
    toolbar.start()
    return toolbar
