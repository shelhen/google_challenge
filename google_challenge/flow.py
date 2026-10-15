"""将 Enterprise 协议令牌提交到当前 Google 挑战表单。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from . import config
from .enterprise.client import EnterpriseUserVerifyClient
from .response import ResponseTools


class GoogleChallengeFlow:
    """复用单个网络会话执行取页面、人工验证、业务提交和保存响应。"""

    def __init__(self, *, proxy: str | None = config.PROXY, on_event=None):
        """创建内部会话，配置代理、TLS 和统一请求头。"""
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = config.VERIFY_TLS
        self.session.max_redirects = config.ENTERPRISE_MAX_REDIRECTS
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.proxy = proxy
        self.on_event = on_event
        self.session.headers.update(
            {"User-Agent": config.DEFAULT_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        )

    def _record_response(self, stage: str, response) -> None:
        """向调试入口报告实际响应，保留原始正文和响应头。"""
        if self.on_event is not None:
            self.on_event(
                stage,
                {
                    "status": response.status_code,
                    "url": response.url,
                    "headers": dict(response.headers),
                    "body": response.content,
                },
            )

    def run(self, url: str, *, answer_provider=None, output_file=None) -> dict:
        """处理当前有效的挑战页，返回协议结果及最终 HTTP 响应分类。"""
        first = self.session.get(url, timeout=config.HTTP_TIMEOUT)
        first.encoding = "utf-8"
        self._record_response("first_response", first)
        result = {"first_status": first.status_code, "first_url": first.url}
        context = ResponseTools.extract_enterprise_context(first.text, first.url)
        final_response = first
        if ResponseTools.is_enterprise_form(first.text):
            if not all(context[key] for key in ("site_key", "challenge_s", "q", "continue_url")):
                raise ValueError("当前挑战表单缺少 sitekey、data-s、q 或 continue。")
            parsed = urlsplit(first.url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            with EnterpriseUserVerifyClient(
                site_key=context["site_key"],
                origin=origin,
                challenge_s=context["challenge_s"],
                page_url=first.url,
                user_agent=self.session.headers["User-Agent"],
                proxy=self.proxy,
                session=self.session,
            ) as client:
                protocol = client.solve(answer_provider=answer_provider)
            result["protocol"] = protocol
            if self.on_event is not None:
                self.on_event("protocol_result", protocol)
            if not protocol.get("ok") or not protocol.get("response_token"):
                result.update(ok=False, error=protocol.get("error", "验证码协议未完成。"))
                return result
            response = self.session.post(
                context["form_url"],
                data={
                    "q": context["q"],
                    "continue": context["continue_url"],
                    "g-recaptcha-response": protocol["response_token"],
                },
                headers={"Origin": origin, "Referer": first.url},
                timeout=config.HTTP_TIMEOUT,
                allow_redirects=False,
            )
            result["submit_status"] = response.status_code
            self._record_response("submit_response", response)
            final_response = response
            location = response.headers.get("Location")
            if response.status_code in {301, 302, 303} and location:
                final_response = self.session.get(
                    urljoin(response.url, location),
                    headers={"Referer": response.url},
                    timeout=config.HTTP_TIMEOUT,
                )
        destination = (
            Path(output_file) if output_file else config.OUTPUT_DIR / "final_response.html"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(final_response.content)
        self._record_response("final_response", final_response)
        kind = ResponseTools.classify_response(
            final_response.status_code, final_response.url, final_response.text
        )
        result.update(
            ok=kind == "ok",
            final_status=final_response.status_code,
            final_url=final_response.url,
            final_kind=kind,
            final_bytes=len(final_response.content),
            html_file=str(destination.resolve()),
            business_verified=None,
            business_verification_status="需结合最终页面内容确认",
        )
        return result

    def close(self) -> None:
        """关闭本流程创建的网络会话。"""
        self.session.close()

    def __enter__(self):
        """进入业务流程上下文。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """退出上下文并关闭会话。"""
        self.close()
