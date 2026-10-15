"""PyCharm 直接运行：真实 HTTP 请求与 Chrome 原生挑战执行。"""

from contextlib import nullcontext

from google_challenge import ChromeService, GoogleSgssClient, config
from google_challenge.debug import DebugRun

# True 自动启动并清理本包专属 Chrome；False 连接已运行的本地服务。
TARGET_URL = "https://www.google.com.hk/search?q=iphone&udm=2"
PROXY = config.PROXY
HTTP_TIMEOUT = config.HTTP_TIMEOUT
EXECUTION_TIMEOUT_MS = config.SGSS_TIMEOUT_MS
AUTO_START_SERVICE = True
PASS_INITIAL_COOKIES = False
PRINT_FULL_RESULT = False
HTML_FILE = None  # 可填已保存的 HTML 路径，仅执行该页面。
PAGE_URL = TARGET_URL
INITIAL_COOKIES = []  # 仅 HTML 重放使用，例如 [{"name": "seed", "value": "value"}]。


def main() -> dict:
    """执行一次真实 Chrome 请求链，保存每个阶段的返回数据。"""
    with DebugRun("sgss_chrome") as debug:
        context = ChromeService() if AUTO_START_SERVICE else nullcontext()
        with context:
            client = GoogleSgssClient(engine="chrome", timeout=HTTP_TIMEOUT, on_event=debug.record)
            debug.record("chrome_health", client.engine.health())
            if HTML_FILE:
                from pathlib import Path

                html = Path(HTML_FILE).read_text(encoding="utf-8")
                result = client.execute(
                    html, PAGE_URL, timeout_ms=EXECUTION_TIMEOUT_MS, cookies=INITIAL_COOKIES
                )
                debug.record("execution_result", result)
            else:
                result = client.get_cookie(
                    TARGET_URL,
                    proxy=PROXY,
                    timeout_ms=EXECUTION_TIMEOUT_MS,
                    pass_initial_cookies=PASS_INITIAL_COOKIES,
                )
            debug.record("chrome_health_after", client.engine.health())
        debug.finish(result, print_full=PRINT_FULL_RESULT)
        return result


if __name__ == "__main__":
    result = main()
