"""统一 SG_SS 客户端，复用相同的请求流程和成功判定。"""

from __future__ import annotations

from typing import Any, Callable

from .. import config
from ..response import ResponseTools, _ms, _now


class GoogleSgssClient(ResponseTools):
    """通过 IV8 或 Chrome 生成 SG_SS，并执行一次携带 Cookie 的重试。"""

    def __init__(
        self,
        engine: str | None = None,
        *,
        timeout: int | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        """选择执行引擎；IV8 和 curl_cffi 均在实际调用时加载。"""
        self.engine_name = config.SGSS_ENGINE if engine is None else engine
        self.timeout = config.HTTP_TIMEOUT if timeout is None else timeout
        self.on_event = on_event
        if self.engine_name == "iv8":
            from .iv8 import Iv8SgssRuntime

            self.engine = Iv8SgssRuntime()
        elif self.engine_name == "chrome":
            from .chrome import ChromeSgssEngine

            self.engine = ChromeSgssEngine()
        else:
            raise ValueError("engine 只能是 iv8 或 chrome。")

    def _emit(self, stage: str, data: dict) -> None:
        """向调用方报告真实请求阶段，便于保存响应和设置断点。"""
        if self.on_event is not None:
            self.on_event(stage, data)

    def _record_response(self, stage: str, response) -> None:
        """存在调试回调时才读取并报告响应正文和响应头。"""
        if self.on_event is not None:
            self._emit(
                stage,
                {
                    "status": response.status_code,
                    "url": response.url,
                    "headers": dict(response.headers),
                    "body": response.content,
                },
            )

    def execute(self, html: str, page_url: str, *, timeout_ms=None, cookies=None) -> dict:
        """直接执行已有 HTML；初始 Cookie 注入仅支持 Chrome 引擎。"""
        if self.engine_name == "chrome":
            return self.engine.generate(html, page_url, timeout_ms, cookies)
        if cookies:
            raise ValueError("IV8 接口暂不支持初始 Cookie 注入，请使用 Chrome 引擎。")
        return self.engine.generate(html, page_url, timeout_ms)

    def get_sg_ss(self, html: str, page_url: str, **kwargs) -> str:
        """执行 HTML，成功时返回 SG_SS，失败时抛出异常。"""
        result = self.execute(html, page_url, **kwargs)
        if not result.get("ok") or not result.get("sg_ss"):
            raise RuntimeError(f"未取得 SG_SS：{result.get('error', '未知错误')}")
        return result["sg_ss"]

    def get_cookie(
        self,
        url: str,
        *,
        proxy: str | None = config.PROXY,
        timeout_ms: int = config.SGSS_TIMEOUT_MS,
        pass_initial_cookies: bool = False,
    ) -> dict[str, Any]:
        """首次请求、执行挑战并重试一次，返回 Cookie、响应分类和耗时。"""
        from curl_cffi import requests

        if pass_initial_cookies and self.engine_name != "chrome":
            raise ValueError("初始 Cookie 注入仅支持 Chrome 引擎。")
        started = _now()
        timings = {}
        proxies = self._proxy_dict(proxy)
        with requests.Session(
            impersonate=config.HTTP_IMPERSONATE, verify=config.VERIFY_TLS, trust_env=False
        ) as session:
            session.headers = self.build_headers(url)
            self._emit("request_started", {"url": url, "engine": self.engine_name})
            step = _now()
            first = session.get(url, proxies=proxies, timeout=self.timeout)
            self._record_response("first_response", first)
            timings["first_request"] = _ms(step)
            first_kind = self.classify_response(first.status_code, first.url, first.text)
            result = {
                "ok": False,
                "engine": self.engine_name,
                "input_url": url,
                "first_status": first.status_code,
                "first_url": first.url,
                "first_kind": first_kind,
                "is_challenge": first_kind == "sg_challenge",
                "timings_ms": timings,
            }
            if first_kind == "sg_challenge":
                result["challenge_executed"] = True
                initial_cookies = None
                if pass_initial_cookies:
                    initial_cookies = [
                        {
                            "name": cookie.name,
                            "value": cookie.value,
                            "domain": cookie.domain,
                            "path": cookie.path or "/",
                            "secure": bool(cookie.secure),
                        }
                        for cookie in session.cookies.jar
                    ]
                step = _now()
                self._emit("execution_started", {"engine": self.engine_name, "url": first.url})
                execution = self.execute(
                    first.text, first.url, timeout_ms=timeout_ms, cookies=initial_cookies
                )
                timings["execution"] = _ms(step)
                result["execution"] = execution
                self._emit("execution_result", execution)
                if not execution.get("ok") or not execution.get("sg_ss"):
                    result["error"] = execution.get("error", "未生成 SG_SS")
                else:
                    sg_ss = execution["sg_ss"]
                    domain = self.cookie_domain(first.url)
                    session.cookies.set("SG_SS", sg_ss, domain=domain, path="/")
                    step = _now()
                    retry = session.get(
                        self.retry_url(first.url, str(self.pick_var(first.text, "eid") or "")),
                        proxies=proxies,
                        timeout=self.timeout,
                        allow_redirects=True,
                    )
                    self._record_response("retry_response", retry)
                    timings["retry_request"] = _ms(step)
                    kind = self.classify_response(retry.status_code, retry.url, retry.text)
                    result.update(
                        ok=kind == "ok",
                        retry_status=retry.status_code,
                        retry_url=retry.url,
                        retry_kind=kind,
                        retry_text=retry.text,
                        sg_ss=sg_ss,
                        sg_ss_len=len(sg_ss),
                        domain=domain,
                        path="/",
                        initial_cookies_passed=pass_initial_cookies,
                    )
            else:
                result.update(ok=first_kind == "ok", challenge_executed=False)
                if first_kind != "ok":
                    result["error"] = first_kind
            result["cookies"] = session.cookies.get_dict()
            result["cookie_header"] = "; ".join(
                f"{name}={value}" for name, value in result["cookies"].items()
            )
            timings["total"] = _ms(started)
            result["total_ms"] = timings["total"]
            self._emit("completed", result)
            return result
