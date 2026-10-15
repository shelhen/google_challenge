"""为直接运行入口统一中文输出编码，不在导入包时修改终端。"""

from __future__ import annotations

import sys

from . import config


def configure_console() -> None:
    """统一标准输出和错误输出编码，兼容 Windows 终端及重定向管道。"""
    if sys.platform == "win32":
        import ctypes

        # 直接连接控制台时同步输出代码页；没有控制台时由流编码负责。
        ctypes.windll.kernel32.SetConsoleOutputCP(config.WINDOWS_CONSOLE_CODE_PAGE)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding=config.CONSOLE_ENCODING, errors="backslashreplace")
