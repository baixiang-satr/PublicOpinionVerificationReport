from types import SimpleNamespace

from src.webui.dpi import enable_windows_dpi_awareness


class _User32:
    def __init__(self, result=True) -> None:
        self.result = result
        self.context_calls = 0
        self.legacy_calls = 0

    def SetProcessDpiAwarenessContext(self, _context) -> bool:
        self.context_calls += 1
        return self.result

    def SetProcessDPIAware(self) -> bool:
        self.legacy_calls += 1
        return True


class _Shcore:
    def __init__(self) -> None:
        self.calls = 0

    def SetProcessDpiAwareness(self, value: int) -> None:
        self.calls += 1
        assert value == 2


def test_dpi_setup_prefers_per_monitor_v2() -> None:
    user32 = _User32(result=True)
    shcore = _Shcore()

    assert enable_windows_dpi_awareness(SimpleNamespace(user32=user32, shcore=shcore))
    assert user32.context_calls == 1
    assert shcore.calls == 0


def test_dpi_setup_falls_back_to_shcore() -> None:
    user32 = _User32(result=False)
    shcore = _Shcore()

    assert enable_windows_dpi_awareness(SimpleNamespace(user32=user32, shcore=shcore))
    assert shcore.calls == 1
    assert user32.legacy_calls == 0
