"""共用的页面识别、表单提取、请求头和计时工具。"""

from __future__ import annotations

import copy
import json
import re
import time
from html.parser import HTMLParser
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from . import config


def _ms(start: float) -> int:
    """计算从指定时刻起经过的毫秒数。"""
    return int(round((time.perf_counter() - start) * 1000))


def _now() -> float:
    """读取单调时钟。"""
    return time.perf_counter()


class _EnterpriseFormParser(HTMLParser):
    """提取当前页面中的 Enterprise 验证表单。"""

    def __init__(self) -> None:
        """初始化实例及运行参数。"""
        super().__init__(convert_charrefs=True)
        self.widget: Dict[str, str] = {}
        self.hidden: Dict[str, str] = {}
        self.form_action = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        """提取验证码组件属性、隐藏字段和表单提交地址。"""
        values = {name.lower(): str(value or "") for name, value in attrs}
        if tag == "form" and values.get("id") == "captcha-form":
            self.form_action = values.get("action") or "index"
        elif tag == "input" and values.get("name") in {"q", "continue"}:
            self.hidden[values["name"]] = values.get("value") or ""
        elif tag in {"div", "span"}:
            classes = set(values.get("class", "").split())
            if "g-recaptcha" in classes and (not self.widget):
                self.widget = values


class ResponseTools:
    """共用的响应识别、请求头和挑战参数工具。"""

    @staticmethod
    def build_headers(url: str) -> Dict[str, str]:
        """复制统一请求头配置，并绑定本次页面的来源地址。"""
        headers = dict(config.SGSS_HEADERS)
        headers["referer"] = url
        return headers

    @staticmethod
    def pick_var(html: str, name: str) -> Optional[Any]:
        """提取 HTML 内指定的变量值，优先解析 JSON。"""
        ms = list(re.finditer("var\\s+" + re.escape(name) + "\\s*=\\s*([\\s\\S]*?);", html))
        if not ms:
            return None
        raw = ms[-1].group(1).strip()
        if raw.startswith("'") and raw.endswith("'") or (raw.startswith('"') and raw.endswith('"')):
            return raw[1:-1].replace("\\x3d", "=").replace("\\/", "/")
        try:
            return json.loads(raw)
        except Exception:
            return raw

    @staticmethod
    def is_challenge(html: str) -> bool:
        """判断页面是否包含 SG_SS 挑战特征。"""
        return "SG_SS" in html and "knitsail" in html

    @staticmethod
    def retry_url(url: str, eid: str) -> str:
        """移除旧挑战参数，并加入本次 eid 对应的 sei 参数。"""
        p = urlparse(url)
        q = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k not in ("sg_ss", "sei", "eid")
        ]
        q.append(("sei", eid))
        return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))

    @staticmethod
    def cookie_domain(url: str) -> str:
        """按域名边界确定 SG_SS 所属的 Cookie 域。"""
        host = urlparse(url).hostname or "www.google.com.hk"
        if host == "google.com.hk" or host.endswith(".google.com.hk"):
            return ".google.com.hk"
        if host == "google.com" or host.endswith(".google.com"):
            return ".google.com"
        return host

    @staticmethod
    def _proxy_dict(proxy: Optional[str]) -> Optional[Dict[str, str]]:
        """将显式代理地址转换为 HTTP 会话参数。"""
        proxy = (proxy or "").strip()
        return {"http": proxy, "https": proxy} if proxy else None

    @classmethod
    def build_navigation_headers(cls, url: str, *, initial: bool = False) -> Dict[str, str]:
        """构造当前页面导航请求的请求头。"""
        return cls.build_headers(url)

    @classmethod
    def extract_enterprise_context(cls, html: str, page_url: str) -> Dict[str, str]:
        """解析当前 Enterprise 表单及其挑战绑定字段。"""
        parser = _EnterpriseFormParser()
        parser.feed(str(html or ""))
        parser.close()
        return {
            "site_key": parser.widget.get("data-sitekey") or "",
            "challenge_s": parser.widget.get("data-s") or "",
            "q": parser.hidden.get("q") or "",
            "continue_url": parser.hidden.get("continue") or "",
            "form_url": urljoin(str(page_url or ""), parser.form_action or "index"),
        }

    @staticmethod
    def is_enterprise_form(html: str) -> bool:
        """识别包含验证码组件的挑战表单。"""
        source = str(html or "")
        return "captcha-form" in source and "g-recaptcha" in source and ("data-sitekey" in source)

    @classmethod
    def classify_response(cls, status: int, url: str, html: str) -> str:
        """按状态码、地址和正文区分成功页面及不同挑战。"""
        if cls.is_enterprise_form(html):
            return "enterprise_form"
        if "/sorry/" in urlparse(url).path:
            return "sorry_page"
        if cls.is_challenge(html):
            return "sg_challenge"
        if status == 429:
            return "http_429"
        if not 200 <= status < 300:
            return "http_error"
        return "ok"

    @staticmethod
    def enterprise_browser_environment() -> Dict[str, Any]:
        """返回与 SG_SS 基线一致的独立浏览器环境副本。"""
        environment = copy.deepcopy(config.SGSS_BROWSER_ENVIRONMENT)
        environment["userAgentData"] = copy.deepcopy(config.SGSS_UA_DATA)
        return environment
