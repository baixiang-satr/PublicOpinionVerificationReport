"""QApplication setup and desktop entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from src.ui.main_window import MainWindow
from src.ui.theme import APP_STYLESHEET


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    instance = QApplication.instance()
    if instance is not None:
        return instance
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(list(arguments) if arguments is not None else sys.argv)
    app.setApplicationName("舆情验证报告工具")
    app.setOrganizationName("PublicOpinionVerificationReport")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    return app


def run_app(arguments: Sequence[str] | None = None) -> int:
    app = create_application(arguments)
    window = MainWindow()
    window.show()
    return app.exec_()
