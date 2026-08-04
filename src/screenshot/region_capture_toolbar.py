"""Native, page-independent toolbar for interactive evidence capture."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
        self._error: BaseException | None = None
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
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
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
        except BaseException as error:  # noqa: BLE001 - forwarded to caller
            self._error = error
            self._ready.set()
        finally:
            self._closed.set()


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
