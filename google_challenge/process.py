"""定位运行程序，并为 Windows 后台进程提供统一选项。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import config


def find_node() -> Path:
    """返回 Node.js 路径，优先使用配置文件中的显式路径。"""
    candidate = str(config.NODE_EXE) if config.NODE_EXE else shutil.which("node")
    if candidate and Path(candidate).is_file():
        return Path(candidate)
    raise FileNotFoundError("未找到 Node.js，请在 config.py 设置 NODE_EXE。")


def find_chrome() -> Path:
    """返回 Chrome 路径，支持 Windows、macOS 和 Linux。"""
    if config.CHROME_PATH:
        candidate = Path(config.CHROME_PATH).expanduser()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Chrome 配置路径不存在：{candidate}")
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for name in ("google-chrome", "chromium", "chromium-browser"):
        discovered = shutil.which(name)
        if discovered:
            candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 Chrome，请在 config.py 设置 CHROME_PATH。")


def hidden_process_flags() -> int:
    """Windows 隐藏后台子进程窗口，其他系统返回零。"""
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
