"""Chrome RPC 客户端与受管理的服务生命周期。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from .. import config
from ..process import find_chrome, find_node, hidden_process_flags


class BrowserRpcError(RuntimeError):
    """携带 HTTP 状态和服务端错误详情的异常。"""

    def __init__(self, status: int, data: dict) -> None:
        """保存错误信息并构造可读消息。"""
        self.status = status
        self.data = data
        super().__init__(f"浏览器服务错误 {status}：{data}")


class ChromeSgssEngine:
    """连接独立 Chrome 服务；不隐式启动或停止服务。"""

    def __init__(self, service_url: str | None = None, token: str | None = None):
        """使用统一配置或指定地址连接本地 RPC。"""
        self.base_url = service_url or f"http://{config.RPC_HOST}:{config.RPC_PORT}"
        self.base_url = self.base_url.rstrip("/").removesuffix("/sgss")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("service_url 必须是有效的 HTTP 服务地址。")
        self.token = config.RPC_TOKEN if token is None else token
        self._opener = build_opener(ProxyHandler({}))

    def _request(self, endpoint: str, payload: dict | None = None, timeout=5) -> dict:
        """直接请求 RPC，禁止使用系统代理转发页面和 Cookie。"""
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.base_url + endpoint, data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read())
            except (ValueError, UnicodeError):
                detail = {"error": str(exc)}
            raise BrowserRpcError(exc.code, detail) from exc
        except URLError as exc:
            raise RuntimeError("无法连接 Chrome 服务，请先启动 ChromeService。") from exc
        if not result.get("ok"):
            raise BrowserRpcError(200, result)
        return result

    def health(self) -> dict:
        """读取服务状态、浏览器进程号和活跃上下文数。"""
        return self._request("/health")

    def generate(self, html: str, page_url: str, timeout_ms=None, cookies=None) -> dict:
        """在独立浏览器上下文中执行 HTML 并返回 SG_SS。"""
        timeout_ms = config.SGSS_TIMEOUT_MS if timeout_ms is None else timeout_ms
        payload = {"html": html, "url": page_url, "timeoutMs": timeout_ms}
        if cookies is not None:
            payload["cookies"] = cookies
        return self._request("/sgss", payload, timeout=timeout_ms / 1000 + 10)


class ChromeService:
    """启动本包专属 Chrome 服务，并在关闭时清理进程和临时文件。"""

    def __init__(self):
        """初始化服务句柄；此时不会创建进程。"""
        self.process: subprocess.Popen | None = None
        self._settings_path: Path | None = None
        self._log = None
        self.engine = ChromeSgssEngine()

    def start(self):
        """启动独立服务，等待服务就绪或报告启动错误。"""
        if self.process is not None:
            raise RuntimeError("该服务实例已启动。")
        settings = {
            "port": config.RPC_PORT,
            "cdp_port": config.CDP_PORT,
            "token": config.RPC_TOKEN,
            "max_jobs": config.RPC_MAX_JOBS,
            "max_body_bytes": config.RPC_MAX_BODY_BYTES,
            "chrome_path": str(find_chrome()),
            "user_agent": config.DEFAULT_UA,
        }
        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", dir=config.TEMP_DIR, delete=False
        ) as stream:
            json.dump(settings, stream)
            self._settings_path = Path(stream.name)
        self._log = tempfile.TemporaryFile(mode="w+b")
        try:
            self.process = subprocess.Popen(
                [str(find_node()), str(config.CHROME_RUNNER_FILE), str(self._settings_path)],
                stdout=self._log,
                stderr=self._log,
                creationflags=hidden_process_flags(),
            )
            deadline = time.monotonic() + config.CHROME_START_TIMEOUT
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self._log.seek(0)
                    raise RuntimeError(self._log.read().decode("utf-8", errors="replace"))
                try:
                    status = self.engine.health()
                    if status.get("service_pid") == self.process.pid:
                        return self
                except (RuntimeError, OSError):
                    pass
                time.sleep(0.1)
            raise TimeoutError("Chrome 服务启动超时，检查配置端口是否被占用。")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """只关闭本实例创建的服务，优先通知 Node 正常清理 Chrome。"""
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    if self.engine.health().get("service_pid") == self.process.pid:
                        self.engine._request("/shutdown", {})
                    self.process.wait(timeout=10)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    self.process.terminate()
                    self.process.wait(timeout=5)
            self.process = None
        if self._settings_path is not None:
            self._settings_path.unlink(missing_ok=True)
            self._settings_path = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self):
        """启动服务并进入上下文。"""
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        """退出上下文并清理服务。"""
        self.close()
