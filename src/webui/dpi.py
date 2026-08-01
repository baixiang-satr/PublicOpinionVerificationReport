"""Windows DPI-awareness setup before WebView2 creates any window."""

from __future__ import annotations

import ctypes
import os
from typing import Any


def enable_windows_dpi_awareness(windll: Any | None = None) -> bool:
    """Prefer per-monitor-v2 awareness and fall back on older Windows APIs."""

    if os.name != "nt":
        return False
    libraries = windll or ctypes.windll
    try:
        if libraries.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        libraries.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:
        pass
    try:
        return bool(libraries.user32.SetProcessDPIAware())
    except Exception:
        return False
