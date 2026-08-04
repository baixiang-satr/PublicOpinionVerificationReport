"""Record a short real-EXE usage walkthrough to an MP4 file."""

from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import subprocess
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageGrab
import win32api
import win32clipboard
import win32con
import win32gui
import win32process


FPS = 10


def _dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def _window_for_pid(pid: int, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def collect(hwnd: int, _value: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            _thread, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid == pid and win32gui.GetWindowText(hwnd):
                matches.append(hwnd)

        win32gui.EnumWindows(collect, None)
        if matches:
            return max(
                matches,
                key=lambda hwnd: _window_area(win32gui.GetWindowRect(hwnd)),
            )
        time.sleep(0.25)
    raise RuntimeError("Timed out waiting for the packaged application window.")


def _window_area(rect: tuple[int, int, int, int]) -> int:
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", size)


class Recorder:
    def __init__(self, output: Path) -> None:
        self.width = win32api.GetSystemMetrics(0)
        self.height = win32api.GetSystemMetrics(1)
        output.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(
            str(output), fourcc, FPS, (self.width, self.height)
        )
        if not self.writer.isOpened():
            raise RuntimeError("Unable to create MP4 video writer.")
        self.caption_font = _font(27)
        self.small_font = _font(20)

    def frame(self, caption: str, detail: str = "") -> None:
        image = ImageGrab.grab(all_screens=True).convert("RGB")
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        top = self.height - (112 if detail else 78)
        draw.rounded_rectangle(
            (38, top, self.width - 38, self.height - 24),
            radius=16,
            fill=(24, 30, 40, 218),
        )
        draw.text((64, top + 13), caption, font=self.caption_font, fill="white")
        if detail:
            draw.text(
                (64, top + 57),
                detail,
                font=self.small_font,
                fill=(192, 218, 255),
            )
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        self.writer.write(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))

    def hold(self, seconds: float, caption: str, detail: str = "") -> None:
        deadline = time.monotonic() + seconds
        interval = 1 / FPS
        while time.monotonic() < deadline:
            started = time.monotonic()
            self.frame(caption, detail)
            time.sleep(max(0, interval - (time.monotonic() - started)))

    def move_click(
        self,
        target: tuple[int, int],
        caption: str,
        detail: str = "",
    ) -> None:
        start = win32api.GetCursorPos()
        for step in range(1, FPS + 1):
            ratio = step / FPS
            point = (
                round(start[0] + (target[0] - start[0]) * ratio),
                round(start[1] + (target[1] - start[1]) * ratio),
            )
            win32api.SetCursorPos(point)
            self.frame(caption, detail)
            time.sleep(1 / FPS)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)

    def close(self) -> None:
        self.writer.release()


def _clipboard_text(value: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(value, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _key(vk: int, *, up: bool = False) -> None:
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP if up else 0, 0)


def _paste_file(path: Path) -> None:
    _clipboard_text(str(path.resolve()))
    _key(win32con.VK_MENU)
    _key(ord("N"))
    _key(ord("N"), up=True)
    _key(win32con.VK_MENU, up=True)
    time.sleep(0.3)
    _key(win32con.VK_CONTROL)
    _key(ord("V"))
    _key(ord("V"), up=True)
    _key(win32con.VK_CONTROL, up=True)
    time.sleep(0.3)
    _key(win32con.VK_RETURN)
    _key(win32con.VK_RETURN, up=True)


def _close_foreground_window() -> None:
    _key(win32con.VK_MENU)
    _key(win32con.VK_F4)
    _key(win32con.VK_F4, up=True)
    _key(win32con.VK_MENU, up=True)


def _scroll_down() -> None:
    for _ in range(8):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -120)
        time.sleep(0.08)


def record(executable: Path, input_file: Path, output: Path) -> None:
    _dpi_aware()
    process = subprocess.Popen([str(executable)], cwd=str(executable.parent))
    recorder: Recorder | None = None
    try:
        hwnd = _window_for_pid(process.pid)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(4)
        recorder = Recorder(output)
        width, height = recorder.width, recorder.height
        recorder.hold(
            4,
            "1. 启动便携版：双击“舆情验证报告工具.exe”",
            "程序、浏览器运行时和 OCR 已随包携带，无需安装 Python。",
        )
        recorder.move_click(
            (round(width * 0.34), round(height * 0.32)),
            "2. 选择“新建采集任务”",
        )
        recorder.hold(3, "进入第 1 步：选择 URL 文件并确认参数")
        recorder.move_click(
            (round(width * 0.195), round(height * 0.37)),
            "选择包含网页链接的 TXT、CSV 或 XLSX 文件",
        )
        recorder.hold(2, "在系统文件窗口选择本次 URL 清单")
        _paste_file(input_file)
        time.sleep(3)
        recorder.hold(
            4,
            "已识别 13 条有效 URL；并发、超时、重试和截图格式可按需调整",
        )
        win32api.SetCursorPos((round(width * 0.78), round(height * 0.78)))
        _scroll_down()
        recorder.hold(2, "开始前先检查本次涉及平台的登录态")
        recorder.move_click(
            (round(width * 0.21), round(height * 0.81)),
            "打开“管理平台登录态”",
        )
        time.sleep(3)
        recorder.hold(
            5,
            "绿色表示登录态有效；橙色项目会在访问目标 URL 前暂停",
            "程序不会回退到游客模式，也不会生成游客身份截图。",
        )
        recorder.move_click(
            (round(width * 0.82), round(height * 0.817)),
            "检查完成后关闭登录态管理中心",
        )
        time.sleep(2)
        recorder.hold(
            4,
            "确认登录态后点击“开始抓取”",
            "任务会依次提取字段、生成内容页/可用作者页截图，并写入 template.zip。",
        )
        recorder.hold(
            5,
            "抓取完成后按左侧步骤预览、补录并导出",
            "详细操作、异常处理和迁移说明见随包《用户使用说明书》。",
        )
    finally:
        if recorder is not None:
            recorder.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record(args.exe.resolve(), args.input.resolve(), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
