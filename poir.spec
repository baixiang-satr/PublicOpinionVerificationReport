# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：舆情验证报告工具（onedir、无控制台窗口）。

配套脚本：``python tools/build_release.py`` 会在本 spec 构建完成后，
把 template/、web/dist/、ms-playwright/ 复制到 exe 同级目录并生成使用说明。
"""
from PyInstaller.utils.hooks import collect_all

datas = [("src/libs/stealth.min.js", "src/libs")]
binaries = []
hiddenimports = [
    "clr",
    "tkinter",
    "_tkinter",
    "win32timezone",
]
# 平台专用提取器是 importlib 动态加载的（registry._DEFAULT_MODULES）
for _module in (
    "douyin",
    "kuaishou",
    "xiaohongshu",
    "weibo",
    "zhihu",
    "tieba",
    "baijiahao",
    "wechat",
    "bytedance_ssr",
    "netease_news",
    "sohu_video",
    "bilibili",
):
    hiddenimports.append(f"src.crawler.platforms.{_module}")

for _package in ("playwright", "webview"):
    _datas, _binaries, _hidden = collect_all(_package)
    datas += _datas
    binaries += _binaries
    hiddenimports += _hidden


a = Analysis(
    ["src/main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="舆情验证报告工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="舆情验证报告工具",
)
