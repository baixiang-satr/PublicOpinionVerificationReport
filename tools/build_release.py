"""一键发布打包：PyInstaller + 资源拷贝 + 生成使用说明。

用法（仓库根目录、已激活 .venv）::

    python tools/build_release.py            # 完整打包
    python tools/build_release.py --skip-browsers   # 不复制 ms-playwright

前置条件：
- 前端已构建（``cd web && npm run build`` 产出 web/dist）；
- 本机已安装 PyInstaller 与 Playwright Chromium
  （``python -m playwright install chromium``）。

产物：``dist/舆情验证报告工具/`` 整个文件夹，压缩后即可拷贝到其他
Windows 电脑直接运行（无需安装 Python、Node 或浏览器）。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "舆情验证报告工具"
SPEC_FILE = PROJECT_ROOT / "poir.spec"
DIST_DIR = PROJECT_ROOT / "dist" / APP_NAME
WORK_DIR = PROJECT_ROOT / "output" / "build-pyi"
RESOURCE_DIRS = (("template", "template"), ("web/dist", "web/dist"))

USAGE_TEXT = """\
舆情验证报告工具 — 使用说明
================================

一、运行环境
------------
- Windows 10 / 11（64 位）。
- 无需安装 Python、Node.js 或任何浏览器，已全部随包携带。
- 需要系统自带 WebView2 运行时（Win10/Win11 一般都已有；
  若启动后窗口空白，请从微软官网搜索 “WebView2 Runtime” 安装后重试）。

二、启动方法
------------
1. 把本文件夹完整解压到任意位置（不要只解压 exe，整个文件夹都要）。
2. 双击「舆情验证报告工具.exe」。
3. 首次启动较慢（约 5-15 秒），属正常现象；若杀毒软件提示，请允许运行。

三、使用流程
------------
按左侧步骤条操作：
1. 欢迎页：新建采集任务，或上传 template.zip 继续补录。
2. 选择 URL 文件（TXT / CSV / XLSX）。
3. 抓取结果：预览表格，点击蓝色链接可打开原页面。
4. 采集与补录：红色空格是必补项；选中行后点「截取内容页 / 截取个人页」，
   在打开的窗口中浏览到目标内容，点「开始框选」后框选屏幕任意区域
   （可包含浏览器地址栏 URL），保存即自动关联到该行。
5. 导出：生成 template.zip 交付包。

四、文件说明
------------
- output/：每次任务的输出目录（template.zip 在里面），自动创建。
- template/：固定交付模板，请勿修改或删除。
- ms-playwright/：随包浏览器，请勿删除。
- 登录态保存在当前 Windows 用户的 AppData 目录（加密存储）；
  换电脑或换 Windows 用户后，需要在「登录态管理」中重新登录平台。

五、常见问题
------------
- 双击没反应：等 15 秒；仍无反应请检查杀毒软件拦截记录。
- 提示缺少 WebView2：安装微软 WebView2 Runtime 后重试。
- 截图窗口打不开：确认没有同时开着其他截图窗口，关闭后重试。
"""


def _run_pyinstaller() -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath",
        str(WORK_DIR),
        "--distpath",
        str(PROJECT_ROOT / "dist"),
        str(SPEC_FILE),
    ]
    print(">>>", " ".join(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def _copy_tree(source: Path, target: Path) -> int:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return sum(1 for path in target.rglob("*") if path.is_file())


def _copy_resources(skip_browsers: bool) -> None:
    for relative, destination in RESOURCE_DIRS:
        source = PROJECT_ROOT / relative
        if not source.is_dir():
            raise SystemExit(f"缺少资源目录 {source}，请先完成前端构建。")
        count = _copy_tree(source, DIST_DIR / destination)
        print(f"copied {relative} -> {destination} ({count} files)", flush=True)
    if skip_browsers:
        print("skipped ms-playwright copy (--skip-browsers)")
        return
    browsers = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "ms-playwright"
    )
    if not browsers.is_dir():
        raise SystemExit(
            f"找不到 Playwright 浏览器目录 {browsers}，"
            "请先运行 python -m playwright install chromium，"
            "或使用 --skip-browsers 跳过。"
        )
    count = _copy_tree(browsers, DIST_DIR / "ms-playwright")
    print(f"copied ms-playwright ({count} files)", flush=True)


def _write_usage() -> None:
    (DIST_DIR / "使用说明.txt").write_text(USAGE_TEXT, encoding="utf-8")


def _folder_size_mb(path: Path) -> float:
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description="舆情验证报告工具一键打包")
    parser.add_argument(
        "--skip-browsers",
        action="store_true",
        help="不复制本机 ms-playwright 浏览器（目标机需自行安装）",
    )
    parser.add_argument(
        "--skip-pyinstaller",
        action="store_true",
        help="跳过 PyInstaller 构建，仅刷新资源与使用说明",
    )
    args = parser.parse_args()
    if not SPEC_FILE.is_file():
        raise SystemExit(f"缺少打包配置 {SPEC_FILE}")
    if not args.skip_pyinstaller:
        _run_pyinstaller()
    if not DIST_DIR.is_dir():
        raise SystemExit(f"PyInstaller 未产出 {DIST_DIR}")
    _copy_resources(args.skip_browsers)
    _write_usage()
    print(f"build-release-ok: {DIST_DIR} ({_folder_size_mb(DIST_DIR):.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
