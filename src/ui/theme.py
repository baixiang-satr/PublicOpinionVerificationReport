"""Elegant, spacious styling for the desktop workflow."""

APP_STYLESHEET = """
/* ═══════════════════════════════════════════════
   全局基础样式
   ═══════════════════════════════════════════════ */
QWidget {
    color: #1a2332;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 14px;
}
QMainWindow, QWidget#appRoot {
    background: #f0f2f5;
}

/* ═══════════════════════════════════════════════
   滚动条
   ═══════════════════════════════════════════════ */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #c0c8d4;
    min-height: 40px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #9aa6b6;
}
QScrollBar::handle:vertical:pressed {
    background: #7a8a9e;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #c0c8d4;
    min-width: 40px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #9aa6b6;
}
QScrollBar::handle:horizontal:pressed {
    background: #7a8a9e;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: none;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* ═══════════════════════════════════════════════
   标题区域
   ═══════════════════════════════════════════════ */
QLabel#appTitle {
    font-size: 28px;
    font-weight: 700;
    color: #0d3b35;
    letter-spacing: 1px;
}
QLabel#appSubtitle {
    font-size: 15px;
    color: #6b7a8d;
    margin-top: 4px;
}
QLabel[muted="true"] {
    color: #7a8898;
    font-size: 13px;
}

/* ═══════════════════════════════════════════════
   步骤提示横幅
   ═══════════════════════════════════════════════ */
QLabel#stepNotice {
    background: #e6f0ed;
    border: 1px solid #c5dbd3;
    border-radius: 8px;
    color: #1d5a4c;
    font-size: 13px;
    line-height: 1.6;
    padding: 12px 16px;
}

/* ═══════════════════════════════════════════════
   分组框
   ═══════════════════════════════════════════════ */
QGroupBox {
    background: #ffffff;
    border: 1px solid #dce2ea;
    border-radius: 10px;
    font-weight: 600;
    font-size: 15px;
    margin-top: 16px;
    padding: 20px 16px 16px 16px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 0 8px;
    color: #1a3440;
}

/* ═══════════════════════════════════════════════
   输入控件
   ═══════════════════════════════════════════════ */
QLineEdit, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #c8d0db;
    border-radius: 6px;
    min-height: 36px;
    padding: 0 10px;
    selection-background-color: #258a74;
    font-size: 14px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 2px solid #258a74;
    padding: 0 9px;
}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background: #f0f3f6;
    color: #8d99a8;
}
QComboBox::drop-down {
    border: none;
    width: 30px;
}
QComboBox::down-arrow {
    image: none;
    border: none;
}
QSpinBox::up-button, QSpinBox::down-button {
    border: none;
    width: 24px;
}

/* ═══════════════════════════════════════════════
   按钮
   ═══════════════════════════════════════════════ */
QPushButton {
    background: #ffffff;
    border: 1px solid #c4cdd9;
    border-radius: 8px;
    min-height: 38px;
    padding: 0 20px;
    font-size: 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #edf2f5;
    border-color: #8d9caa;
}
QPushButton:pressed {
    background: #dfe6ec;
}
QPushButton:disabled {
    background: #eceff2;
    border-color: #d9dee4;
    color: #9da6b0;
}
QPushButton#primaryButton {
    background: #1a7a64;
    border-color: #1a7a64;
    color: #ffffff;
    font-weight: 700;
    font-size: 15px;
    min-height: 42px;
    padding: 0 32px;
}
QPushButton#primaryButton:hover {
    background: #146353;
}
QPushButton#primaryButton:pressed {
    background: #0f5244;
}
QPushButton#cancelButton {
    color: #b54a3e;
    border-color: #e0b5af;
}
QPushButton#cancelButton:hover {
    background: #fdf0ee;
}
QPushButton#retryButton {
    color: #b87d2a;
    border-color: #e2c9a3;
}
QPushButton#retryButton:hover {
    background: #fdf7ed;
}
QPushButton#openOutputButton {
    color: #2e7d6a;
    border-color: #b6d4cb;
}
QPushButton#openOutputButton:hover {
    background: #edf7f3;
}

/* ═══════════════════════════════════════════════
   进度条
   ═══════════════════════════════════════════════ */
QProgressBar {
    background: #e2e7ed;
    border: 0;
    border-radius: 6px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    font-size: 11px;
    color: #566573;
}
QProgressBar::chunk {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #1a7a64,
        stop: 0.6 #28a68a,
        stop: 1 #3dc4a8
    );
    border-radius: 6px;
}

/* ═══════════════════════════════════════════════
   统计卡片
   ═══════════════════════════════════════════════ */
QFrame#statBox {
    background: #f7f9fb;
    border: 1px solid #e0e6ed;
    border-radius: 10px;
    padding: 4px;
}
QLabel#statValue {
    font-size: 22px;
    font-weight: 700;
}

/* ═══════════════════════════════════════════════
   标签页
   ═══════════════════════════════════════════════ */
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #dce2ea;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #e6eaef;
    border: 1px solid #d4dae3;
    border-bottom: 0;
    padding: 10px 22px;
    margin-right: 3px;
    font-size: 13px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:hover {
    background: #edf0f5;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #1a7a64;
    font-weight: 700;
    font-size: 14px;
}

/* ═══════════════════════════════════════════════
   表格
   ═══════════════════════════════════════════════ */
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f8fafc;
    border: 0;
    gridline-color: #e6eaef;
    selection-background-color: #d6ede7;
    selection-color: #1a2332;
    font-size: 13px;
}
QHeaderView::section {
    background: #eef2f6;
    border: 0;
    border-right: 1px solid #dce2ea;
    border-bottom: 2px solid #d0d7df;
    padding: 9px 8px;
    font-weight: 700;
    font-size: 13px;
    color: #3d5166;
}

/* ═══════════════════════════════════════════════
   日志面板
   ═══════════════════════════════════════════════ */
QPlainTextEdit#logText {
    background: #1a2332;
    border: 0;
    border-radius: 6px;
    color: #dce4e9;
    font-family: "Cascadia Mono", "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
    padding: 12px;
}

/* ═══════════════════════════════════════════════
   复选框
   ═══════════════════════════════════════════════ */
QCheckBox {
    spacing: 8px;
    font-size: 14px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #b8c3d0;
    border-radius: 4px;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #1a7a64;
    border-color: #1a7a64;
}
QCheckBox::indicator:hover {
    border-color: #258a74;
}

/* ═══════════════════════════════════════════════
   状态栏
   ═══════════════════════════════════════════════ */
QStatusBar {
    background: #e6eaef;
    color: #4e6278;
    font-size: 13px;
    border-top: 1px solid #d4dae3;
}

/* ═══════════════════════════════════════════════
   工具提示
   ═══════════════════════════════════════════════ */
QToolTip {
    background: #1a2332;
    border: none;
    border-radius: 6px;
    color: #e8ecf0;
    font-size: 13px;
    padding: 8px 12px;
}

/* ═══════════════════════════════════════════════
   当前页面 URL 标签
   ═══════════════════════════════════════════════ */
QLabel#currentUrl {
    background: #f5f7f9;
    border: 1px solid #e0e6ed;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    color: #4e6278;
}

/* ═══════════════════════════════════════════════
   选项说明标签
   ═══════════════════════════════════════════════ */
QLabel#optionHelp {
    font-size: 12px;
    color: #8897a8;
    margin-top: 0;
    padding-left: 4px;
}

/* ═══════════════════════════════════════════════
   分割线
   ═══════════════════════════════════════════════ */
QFrame#separator {
    background: #dce2ea;
    max-height: 1px;
    min-height: 1px;
    margin: 4px 0;
}
"""
