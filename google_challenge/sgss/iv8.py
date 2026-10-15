"""IV8 隔离执行器及异步接口。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import copy
import ctypes
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .. import config
from ..config import (
    DEFAULT_GPU_RENDERER,
    DEFAULT_GPU_VENDOR,
    DEFAULT_UA,
)
from ..response import _ms

# macOS 先加载系统库，避免部分 IV8 扩展导入时找不到符号。
if sys.platform == "darwin":
    try:
        ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
            mode=ctypes.RTLD_GLOBAL,
        )
    except OSError:
        pass

_IV8_CONTEXT_INIT_LOCK = threading.RLock()


@contextlib.contextmanager
def _silence_iv8_stdio():
    """根据配置临时抑制原生 IV8 的标准输出与错误输出。"""
    if config.IV8_VERBOSE:
        yield
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    saved_stdout = saved_stderr = devnull = None
    try:
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        if saved_stdout is not None:
            try:
                os.dup2(saved_stdout, 1)
            except Exception:
                pass
            try:
                os.close(saved_stdout)
            except Exception:
                pass
        if saved_stderr is not None:
            try:
                os.dup2(saved_stderr, 2)
            except Exception:
                pass
            try:
                os.close(saved_stderr)
            except Exception:
                pass
        if devnull is not None:
            try:
                os.close(devnull)
            except Exception:
                pass


@dataclass
class Iv8SgssOptions:
    """IV8 执行器参数，默认值统一来自配置文件。"""

    timeout_ms: int = config.SGSS_TIMEOUT_MS
    user_agent: str = DEFAULT_UA
    inner_width: int = config.IV8_INNER_WIDTH
    inner_height: int = config.IV8_INNER_HEIGHT
    outer_width: int = config.IV8_OUTER_WIDTH
    outer_height: int = config.IV8_OUTER_HEIGHT
    device_pixel_ratio: int = config.IV8_DEVICE_PIXEL_RATIO
    language: str = config.IV8_LANGUAGE
    languages: tuple = config.IV8_LANGUAGES
    connection_downlink: float = config.IV8_CONNECTION_DOWNLINK
    connection_rtt: int = config.IV8_CONNECTION_RTT
    connection_effective_type: str = config.IV8_CONNECTION_EFFECTIVE_TYPE
    time_mode: str = config.IV8_TIME_MODE
    icu_locale: str = config.IV8_ICU_LOCALE
    webgl_enabled: bool = config.IV8_WEBGL_ENABLED
    gpu_vendor: str = DEFAULT_GPU_VENDOR
    gpu_renderer: str = DEFAULT_GPU_RENDERER


class Iv8SgssRuntime:
    """每个挑战创建独立上下文的同步 IV8 执行器。"""

    def __init__(self, opts: Optional[Iv8SgssOptions] = None):
        """初始化实例及运行参数。"""
        self.opts = opts or Iv8SgssOptions()
        self._lock = threading.RLock()

    def _import_iv8(self):
        """按需加载 IV8，避免影响其他执行路径。"""
        try:
            with _silence_iv8_stdio():
                import iv8
            return iv8
        except ImportError as exc:
            raise RuntimeError("当前解释器未安装 iv8，请检查 Conda 环境。") from exc

    def _create_context(self, iv8, page_url: str):
        """使用统一配置创建隔离的 IV8 上下文。"""
        with _IV8_CONTEXT_INIT_LOCK:
            # 仅保存并恢复原进程状态；区域配置来自 config.py。
            previous = os.environ.get("LC_ALL")
            try:
                if self.opts.icu_locale:
                    os.environ["LC_ALL"] = self.opts.icu_locale
                return iv8.JSContext(
                    environment=self._browser_environment(page_url),
                    config=self._browser_config(),
                    time_mode=self.opts.time_mode,
                )
            finally:
                if previous is None:
                    os.environ.pop("LC_ALL", None)
                else:
                    os.environ["LC_ALL"] = previous

    def _browser_config(self) -> Dict[str, Any]:
        """构造 IV8 原生浏览器能力开关。"""
        return {
            "canvas": {
                "context": {
                    name: {"enabled": self.opts.webgl_enabled}
                    for name in ("webgl", "experimental-webgl", "webgl2")
                }
            }
        }

    def _browser_environment(self, page_url: str) -> Dict[str, Any]:
        """复制统一浏览器基线，并应用实例尺寸、语言和 GPU 参数。"""
        options = self.opts
        environment = copy.deepcopy(config.SGSS_BROWSER_ENVIRONMENT)
        navigator = environment["navigator"]
        navigator.update(
            userAgent=options.user_agent,
            appVersion=options.user_agent.replace("Mozilla/", ""),
            language=options.language,
            languages=list(options.languages),
        )
        navigator["connection"].update(
            downlink=options.connection_downlink,
            rtt=options.connection_rtt,
            effectiveType=options.connection_effective_type,
        )
        environment["window"].update(
            innerWidth=options.inner_width,
            innerHeight=options.inner_height,
            outerWidth=options.outer_width,
            outerHeight=options.outer_height,
            devicePixelRatio=options.device_pixel_ratio,
        )
        environment["webgl"].update(
            UNMASKED_VENDOR_WEBGL=options.gpu_vendor, UNMASKED_RENDERER_WEBGL=options.gpu_renderer
        )
        environment["location"] = {"href": page_url}
        return environment

    @staticmethod
    def _preload_js() -> str:
        """读取兼容脚本模板，并注入统一的浏览器品牌数据。"""
        template = (config.RUNTIME_DIR / "iv8_preload.js").read_text(encoding="utf-8")
        return template.replace(
            "/*__SGSS_UA_DATA__*/{}", json.dumps(config.SGSS_UA_DATA, ensure_ascii=False)
        )

    def generate(
        self, html: str, page_url: str, timeout_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        """执行当前页面的脚本并提取 SG_SS。"""
        if not html or not isinstance(html, str):
            raise RuntimeError("missing challenge html")
        timeout_ms = int(timeout_ms or self.opts.timeout_ms)
        t0 = time.perf_counter()
        with self._lock:
            iv8 = self._import_iv8()
            ctx = None
            try:
                with _silence_iv8_stdio():
                    ctx = self._create_context(iv8, page_url)
                    ctx.eval(self._preload_js(), name="sgss_preload.js")
                    snapshot = {"baseURL": page_url, "url": page_url, "html": html}
                    ctx.eval(
                        "window.__iv8__.page.load("
                        + json.dumps(snapshot, ensure_ascii=False)
                        + ");",
                        name="sgss_page_load.js",
                    )
                deadline = time.perf_counter() + timeout_ms / 1000.0
                last: Dict[str, Any] = {}
                samples = []
                rounds = 0
                while time.perf_counter() < deadline:
                    rounds += 1
                    with _silence_iv8_stdio():
                        ctx.eval("window.__iv8__.eventLoop.drainMicrotasks();")
                        ctx.eval("window.__iv8__.eventLoop.advance(0);")
                        ctx.eval("window.__iv8__.eventLoop.drainMicrotasks();")
                        last = ctx.eval(
                            "window.__sgss_read ? window.__sgss_read() : ({href:String(location.href), cookie:String(document.cookie||''), sg_ss:''})",
                            to_py=True,
                        )
                    sg = str((last or {}).get("sg_ss") or "")
                    samples.append(
                        {
                            "round": rounds,
                            "len": len(sg),
                            "cookie_len": len(str(last.get("cookie") or "")),
                            "probe": last.get("probe"),
                        }
                    )
                    samples = samples[-20:]
                    if sg:
                        break
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    wait_start = time.perf_counter()
                    time.sleep(min(0.05, remaining))
                    elapsed_ms = (time.perf_counter() - wait_start) * 1000
                    with _silence_iv8_stdio():
                        ctx.eval(f"window.__iv8__.eventLoop.advance({elapsed_ms});")
                sg_ss = str((last or {}).get("sg_ss") or "")
                output = {
                    "ok": bool(sg_ss),
                    "sg_ss": sg_ss,
                    "sg_ss_len": len(sg_ss),
                    "href": last.get("href"),
                    "cookie": last.get("cookie"),
                    "writes": last.get("writes", []),
                    "probe": last.get("probe"),
                    "errors": last.get("errors", []),
                    "source": last.get("source", ""),
                    "samples": samples[-20:],
                    "rounds": rounds,
                    "ms": _ms(t0),
                    "engine": "iv8",
                    "iv8_version": str(getattr(iv8, "__version__", "unknown")),
                    "v8_version": str(getattr(iv8, "v8_version", "unknown")),
                    "time_mode": self.opts.time_mode,
                    "requested_icu_locale": self.opts.icu_locale,
                    "compatibility": {
                        "browser_equivalent": False,
                        "adapters": [
                            "legacy_events",
                            "permissions_promise",
                            "native_uadata",
                            "document_ready_state",
                            "trusted_policy_callbacks",
                            "non_navigating_anchor_click",
                            "csi_page_clock",
                            "html_document_scripts",
                            "simple_media_whitespace",
                        ],
                        "webgl_enabled": self.opts.webgl_enabled,
                        "known_limits": [
                            *(
                                ["IV8 0.1.3 page.load does not expose the executing currentScript"]
                                if str(getattr(iv8, "__version__", "unknown")) == "0.1.3"
                                else []
                            ),
                            "IV8 does not produce native navigation timing entries",
                            "scripts adapter covers the main ordinary HTML document, not SVG/XML namespace semantics",
                            "media adapter only normalizes simple keyword queries; viewport/range queries remain incomplete",
                            "Canvas drawing, layout and viewport propagation remain non-equivalent",
                            "WebGL metadata is configured from CDP; IV8 shader rendering/readback remains incomplete",
                            "IV8 WebGPU requestAdapter returns null despite availability configuration",
                            "Trusted Types fallback returns strings, not native trusted objects",
                            "JS adapters retain their real JS source and stack frames",
                            "IV8 inline-script attribution differs from page URL/position",
                            "click adapter covers anchors without href, not form activation/navigation",
                            "ICU locale is process-wide and requires a fresh process if initialized earlier",
                        ],
                    },
                }
                if not sg_ss:
                    output["error"] = "timeout/no SG_SS"
                return output
            finally:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:
                        pass


class Iv8SgssEngine:
    """使用专属工作线程调用 IV8 的异步适配器。"""

    def __init__(self, opts: Optional[Iv8SgssOptions] = None):
        """初始化实例及运行参数。"""
        self.runtime = Iv8SgssRuntime(opts)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="iv8-sgss"
        )
        self._closed = False

    async def generate(
        self, html: str, page_url: str, timeout_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        """执行当前页面的脚本并提取 SG_SS。"""
        if self._closed:
            raise RuntimeError("Iv8SgssEngine is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, lambda: self.runtime.generate(html, page_url, timeout_ms)
        )

    async def close(self) -> None:
        """关闭实例持有的资源。"""
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)
