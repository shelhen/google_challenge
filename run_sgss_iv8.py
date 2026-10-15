"""PyCharm 直接运行：真实请求目标页面，使用 IV8 执行 SG_SS 挑战。"""

from google_challenge import GoogleSgssClient, config
from google_challenge.debug import DebugRun

# 修改这些参数，然后右键 Run 或 Debug；默认会实际访问目标 URL。
TARGET_URL = "https://www.google.com.hk/search?q=iphone&udm=2"
PROXY = config.PROXY
HTTP_TIMEOUT = config.HTTP_TIMEOUT
EXECUTION_TIMEOUT_MS = config.SGSS_TIMEOUT_MS
PRINT_FULL_RESULT = False
HTML_FILE = None  # 可填已保存的 HTML 路径，仅执行该页面，便于反复下断点。
PAGE_URL = TARGET_URL  # 重放 HTML 时填写它原来的最终响应 URL。


def main() -> dict:
    """保存首次响应、IV8 诊断、重试响应和最终返回值。"""
    with DebugRun("sgss_iv8") as debug:
        client = GoogleSgssClient(engine="iv8", timeout=HTTP_TIMEOUT, on_event=debug.record)
        # 可在这里进入 get_cookie；或在 debug.record 中查看各阶段实际数据。
        if HTML_FILE:
            from pathlib import Path

            html = Path(HTML_FILE).read_text(encoding="utf-8")
            result = client.execute(html, PAGE_URL, timeout_ms=EXECUTION_TIMEOUT_MS)
            debug.record("execution_result", result)
        else:
            result = client.get_cookie(TARGET_URL, proxy=PROXY, timeout_ms=EXECUTION_TIMEOUT_MS)
        debug.finish(result, print_full=PRINT_FULL_RESULT)
        # 在下一行设置断点，可以直接查看完整 result、client 和 debug.directory。
        return result


if __name__ == "__main__":
    result = main()
