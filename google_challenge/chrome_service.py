"""通过 python -m google_challenge.chrome_service 启动常驻服务。"""

import time

from .console import configure_console
from .sgss.chrome import ChromeService


def main() -> None:
    """保持服务运行，按 Ctrl+C 正常清理浏览器。"""
    configure_console()
    try:
        with ChromeService() as service:
            print(f"Chrome 服务已启动：{service.engine.base_url}", flush=True)
            while service.process.poll() is None:
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("Chrome 服务已关闭。")


if __name__ == "__main__":
    main()
