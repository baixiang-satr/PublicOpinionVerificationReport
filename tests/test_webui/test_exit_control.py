"""U02：窗口关闭确认——否决默认关闭、退出/最小化两动作。"""

from __future__ import annotations

from typing import Any

from src.webui.exit_control import ExitControlMixin, install_close_confirmation


class _Event:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "_Event":
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = type("Events", (), {"closing": _Event()})()
        self.destroyed = False
        self.minimized = False

    def destroy(self) -> None:
        self.destroyed = True

    def minimize(self) -> None:
        self.minimized = True


class _FakeSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


class _Host(ExitControlMixin):
    def __init__(self, window: _FakeWindow) -> None:
        self._window_provider = lambda: window


def test_closing_event_is_vetoed_and_frontend_notified() -> None:
    window = _FakeWindow()
    sink = _FakeSink()

    install_close_confirmation(window, sink)

    assert len(window.events.closing.handlers) == 1
    handler = window.events.closing.handlers[0]
    assert handler() is False  # 否决默认关闭
    assert sink.events == [("app_closing", {})]


def test_confirm_exit_destroys_window() -> None:
    window = _FakeWindow()
    host = _Host(window)

    assert host.confirm_exit() == {"ok": True}
    assert window.destroyed


def test_minimize_window_keeps_process_alive() -> None:
    window = _FakeWindow()
    host = _Host(window)

    assert host.minimize_window() == {"ok": True}
    assert window.minimized
    assert not window.destroyed
