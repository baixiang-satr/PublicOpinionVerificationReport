"""Quiet, high-contrast styling for the desktop workflow."""

APP_STYLESHEET = """
QWidget {
    color: #18212b;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#appRoot { background: #f4f6f8; }
QLabel#appTitle { font-size: 24px; font-weight: 700; color: #153b35; }
QLabel#appSubtitle, QLabel[muted="true"] { color: #637083; }
QLabel#stepNotice {
    background: #e9f3f0;
    border: 1px solid #bfd8d1;
    border-radius: 6px;
    color: #244e47;
    padding: 9px 12px;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #d8dee7;
    border-radius: 6px;
    font-weight: 600;
    margin-top: 12px;
    padding: 12px 10px 8px 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #263747;
}
QLineEdit, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #bdc7d3;
    border-radius: 5px;
    min-height: 32px;
    padding: 0 8px;
    selection-background-color: #2b7768;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 2px solid #2b7768; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background: #eef1f4;
    color: #7a8491;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #b7c1cc;
    border-radius: 5px;
    min-height: 34px;
    padding: 0 14px;
}
QPushButton:hover { background: #f0f4f6; border-color: #81909f; }
QPushButton:pressed { background: #e3e9ed; }
QPushButton:disabled {
    background: #eceff2;
    border-color: #d9dee4;
    color: #929aa4;
}
QPushButton#primaryButton {
    background: #176b5b;
    border-color: #176b5b;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: #12594c; }
QPushButton#cancelButton { color: #a43b31; border-color: #d7aaa5; }
QProgressBar {
    background: #e4e9ee;
    border: 0;
    border-radius: 5px;
    min-height: 10px;
    max-height: 10px;
    text-align: center;
}
QProgressBar::chunk { background: #2b7768; border-radius: 5px; }
QFrame#statBox {
    background: #f8fafb;
    border: 1px solid #dde3ea;
    border-radius: 6px;
}
QLabel#statValue { font-size: 19px; font-weight: 700; }
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #d8dee7;
    border-radius: 4px;
}
QTabBar::tab {
    background: #e9edf1;
    border: 1px solid #d0d7df;
    border-bottom: 0;
    padding: 8px 18px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #ffffff; color: #176b5b; font-weight: 600; }
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f8fafb;
    border: 0;
    gridline-color: #e1e6eb;
    selection-background-color: #dcece8;
    selection-color: #18212b;
}
QHeaderView::section {
    background: #eef2f4;
    border: 0;
    border-right: 1px solid #d7dde4;
    border-bottom: 1px solid #cbd3dc;
    padding: 7px 6px;
    font-weight: 600;
}
QPlainTextEdit {
    background: #19232c;
    border: 0;
    color: #dce4e9;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    padding: 8px;
}
QCheckBox { spacing: 7px; }
QStatusBar { background: #e9edf1; color: #4e5d6c; }
"""
