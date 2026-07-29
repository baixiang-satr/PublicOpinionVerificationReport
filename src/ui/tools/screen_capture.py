"""Fullscreen capture helper for the review workspace.

Hides the application window, waits briefly so the desktop is fully visible,
captures every screen and stitches them into one image following the virtual
desktop geometry, then restores the window.  Pure presentation code; the
resulting :class:`QPixmap` is handed back through the ``captured`` signal.
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, QRect, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget


class FullScreenCapturer(QObject):
    captured = pyqtSignal(QPixmap)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window: QWidget | None = None
        self._was_minimized = False

    def start(self, window: QWidget, *, delay_ms: int = 400) -> None:
        if self._window is not None:
            self.failed.emit("上一次截图尚未完成。")
            return
        self._window = window
        self._was_minimized = window.isMinimized()
        window.showMinimized()
        # ``showMinimized`` still animates; the delay lets the desktop settle.
        QTimer.singleShot(delay_ms, self._grab)

    def _grab(self) -> None:
        window = self._window
        self._window = None
        try:
            pixmap = grab_virtual_desktop()
            if pixmap.isNull():
                raise RuntimeError("系统返回了空图像。")
        except Exception as error:
            self._restore(window)
            self.failed.emit(f"截图失败：{error}")
            return
        self._restore(window)
        self.captured.emit(pixmap)

    def _restore(self, window: QWidget | None) -> None:
        if window is None:
            return
        if self._was_minimized:
            window.showMinimized()
        else:
            window.showNormal()
            window.raise_()
            window.activateWindow()


def grab_virtual_desktop() -> QPixmap:
    """Capture all screens stitched by virtual geometry (DPI-aware pixels)."""

    screens = QGuiApplication.screens()
    if not screens:
        return QPixmap()
    virtual = QRect()
    for screen in screens:
        virtual = virtual.united(screen.geometry())
    dpr = max(screen.devicePixelRatio() for screen in screens)
    canvas = QPixmap(int(virtual.width() * dpr), int(virtual.height() * dpr))
    canvas.fill(QColor("black"))
    painter = QPainter(canvas)
    try:
        for screen in screens:
            shot = screen.grabWindow(0)
            geometry = screen.geometry()
            target = QRect(
                int((geometry.x() - virtual.x()) * dpr),
                int((geometry.y() - virtual.y()) * dpr),
                int(geometry.width() * dpr),
                int(geometry.height() * dpr),
            )
            painter.drawPixmap(target, shot, shot.rect())
    finally:
        painter.end()
    # Keep logical size metadata sane when the image is later scaled in UI.
    canvas.setDevicePixelRatio(1.0)
    return canvas
