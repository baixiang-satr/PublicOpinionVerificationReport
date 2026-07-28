"""Clean styling matching mobile-forensics collector reference."""

# Stylesheet inspired by mobile_forensics/tools/data-extraction/gui/app.py
APP_STYLESHEET = """
QMainWindow, QWidget#appRoot {
    background: #f4f6f7;
    color: #1d292f;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

/* ── Scroll bars ── */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #c4ced2;
    min-height: 30px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #a0adb3;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

/* ── Titles (参考 mobile_forensics collector 标题样式) ── */
QLabel#appTitle {
    font-size: 24px;
    font-weight: 700;
    color: #153438;
}
QLabel#appSubtitle {
    color: #52646b;
}
QLabel[muted="true"] {
    color: #6d7f86;
    font-size: 12px;
}

/* ── Hint banner ── */
QLabel#stepNotice {
    background: #e6f2f0;
    border: 1px solid #b7d8d3;
    border-radius: 6px;
    padding: 10px 12px;
    color: #143c38;
    font-size: 12px;
}
QLabel#assetPolicy {
    background: #f2f8f7;
    border: 1px solid #cfe1de;
    border-radius: 5px;
    padding: 7px 10px;
    color: #315b58;
    font-size: 12px;
}

/* ── Group boxes ── */
QGroupBox {
    background: #ffffff;
    border: 1px solid #d0d8dc;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    margin-top: 10px;
    padding: 16px 12px 12px 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
    color: #153438;
}

/* ── Input controls ── */
QLineEdit, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #b8c4c8;
    border-radius: 4px;
    min-height: 30px;
    padding: 0 8px;
    selection-background-color: #0b6e69;
    font-size: 13px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #0b6e69;
}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background: #eef1f2;
    color: #8d9ba0;
}
QComboBox::drop-down { border: none; width: 26px; }
QSpinBox::up-button, QSpinBox::down-button { border: none; width: 20px; }

/* ── Buttons ── */
QPushButton {
    background: #ffffff;
    border: 1px solid #9eacb1;
    border-radius: 5px;
    min-height: 32px;
    padding: 0 16px;
    font-size: 13px;
}
QPushButton:hover {
    border-color: #0b6e69;
    background: #edf7f5;
}
QPushButton:pressed {
    background: #dcece8;
}
QPushButton:disabled {
    color: #929da1;
    background: #e7ebed;
    border-color: #d0d8dc;
}
QPushButton#primaryButton {
    background: #0b6e69;
    color: #ffffff;
    border: none;
    font-weight: 600;
    font-size: 13px;
    min-height: 34px;
    padding: 0 24px;
}
QPushButton#primaryButton:hover {
    background: #0a5f5b;
}
QPushButton#primaryButton:pressed {
    background: #08504d;
}
QPushButton#cancelButton {
    color: #a84035;
    border-color: #dbaaa5;
}
QPushButton#cancelButton:hover {
    background: #fdf0ee;
    border-color: #a84035;
}
QPushButton#retryButton {
    color: #aa7526;
    border-color: #dbc095;
}
QPushButton#retryButton:hover {
    background: #fdf7ed;
    border-color: #aa7526;
}
QPushButton#openOutputButton {
    color: #0b6e69;
    border-color: #aacac4;
}
QPushButton#openOutputButton:hover {
    background: #edf7f5;
    border-color: #0b6e69;
}

/* ── Progress bar ── */
QProgressBar {
    border: 1px solid #d0d8dc;
    border-radius: 4px;
    background: #ffffff;
    text-align: center;
    font-size: 11px;
    color: #1d292f;
    min-height: 18px;
    max-height: 18px;
}
QProgressBar::chunk {
    background: #0b6e69;
    border-radius: 3px;
}

/* ── Stat cards ── */
QFrame#statBox {
    background: #f8fafb;
    border: 1px solid #dde3e8;
    border-radius: 6px;
    padding: 2px;
}
QLabel#statValue {
    font-size: 20px;
    font-weight: 700;
}

/* ── Tabs ── */
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #d0d8dc;
    border-radius: 5px;
    top: -1px;
}
QTabBar::tab {
    background: #e6eaed;
    border: 1px solid #d0d8dc;
    border-bottom: 0;
    padding: 8px 18px;
    margin-right: 2px;
    font-size: 12px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
}
QTabBar::tab:hover {
    background: #edf0f2;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #0b6e69;
    font-weight: 600;
}

/* ── Table ── */
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7f9fa;
    border: 0;
    gridline-color: #e3e8eb;
    selection-background-color: #d6ede7;
    selection-color: #1d292f;
    font-size: 12px;
}
QHeaderView::section {
    background: #eef2f4;
    border: 0;
    border-right: 1px solid #dce2e6;
    border-bottom: 1px solid #d0d8dc;
    padding: 7px 6px;
    font-weight: 600;
    font-size: 12px;
    color: #3d4f57;
}

/* ── Log text ── */
QPlainTextEdit#logText {
    background: #1a2332;
    border: 0;
    border-radius: 5px;
    color: #dce4e9;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    padding: 10px;
}
QPlainTextEdit#logText QScrollBar:vertical {
    background: #232e3e;
    width: 8px;
}
QPlainTextEdit#logText QScrollBar::handle:vertical {
    background: #3d4f63;
    border-radius: 4px;
    min-height: 30px;
}
QPlainTextEdit#logText QScrollBar::handle:vertical:hover {
    background: #5a7088;
}

/* ── Checkbox ── */
QCheckBox {
    spacing: 6px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #b8c4c8;
    border-radius: 3px;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #0b6e69;
    border-color: #0b6e69;
}

/* ── Status bar ── */
QStatusBar {
    background: #e6eaed;
    color: #4e5f68;
    font-size: 12px;
    border-top: 1px solid #d0d8dc;
}

/* ── Tooltips ── */
QToolTip {
    background: #1d292f;
    border: none;
    border-radius: 4px;
    color: #e8edf0;
    font-size: 12px;
    padding: 6px 10px;
}

/* ── URL label ── */
QLabel#currentUrl {
    background: #f7f9fa;
    border: 1px solid #dde3e8;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 12px;
    color: #4e5f68;
}
"""
