"""Offline regression tests for the native capture toolbar thread."""
from __future__ import annotations

import asyncio
import threading
import tkinter

import pytest

from src.screenshot.region_capture_toolbar import NativeCaptureToolbar


class _ExplodingTk:
    """Fake Tk that fails during widget setup, after interpreter creation."""

    instances: list["_ExplodingTk"] = []

    def __init__(self) -> None:
        self.created_in = threading.current_thread().name
        self.destroyed_in: str | None = None
        _ExplodingTk.instances.append(self)

    def title(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom during setup")

    def destroy(self) -> None:
        self.destroyed_in = threading.current_thread().name


def test_tk_destroyed_in_own_thread_and_error_is_plain_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tk 装配失败时：解释器必须在创建它的线程内销毁，错误不得携带 traceback。

    回归：Tcl_AsyncDelete "async handler deleted by the wrong thread" 致命崩溃。
    """

    monkeypatch.setattr(tkinter, "Tk", _ExplodingTk)
    _ExplodingTk.instances.clear()
    loop = asyncio.new_event_loop()
    try:
        toolbar = NativeCaptureToolbar(
            loop,
            lambda _payload: None,
            evidence_id=1,
            target="content",
        )
        with pytest.raises(RuntimeError, match="无法打开页面外截图控制条"):
            toolbar.start(timeout=5.0)

        assert toolbar._closed.wait(timeout=2.0)
        fake = _ExplodingTk.instances[0]
        assert fake.destroyed_in == fake.created_in
        assert isinstance(toolbar._error, str)
        assert "boom during setup" in toolbar._error
    finally:
        loop.close()


def test_toolbar_opens_and_closes_cleanly_on_real_tk() -> None:
    """真实 Tk 成功路径：start → close 后线程结束且无错误残留。"""

    loop = asyncio.new_event_loop()
    try:
        toolbar = NativeCaptureToolbar(
            loop,
            lambda _payload: None,
            evidence_id=2,
            target="content",
        )
        toolbar.start(timeout=5.0)
        toolbar.hide()
        toolbar.close()

        assert toolbar._closed.wait(timeout=2.0)
        assert toolbar._error is None
        assert not toolbar._thread.is_alive()
    finally:
        loop.close()
