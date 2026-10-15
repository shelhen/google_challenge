"""网络会话、浏览器环境快照和资源缓存。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests

from .. import config
from ..config import CACHE_DIR, DEFAULT_USER_AGENT, RESULT_DIR, TEMP_DIR
from ..process import find_node


class _EnterpriseBase:
    """仅保留 Enterprise 所需的环境、资源与进程辅助方法。"""

    def log(self, message: str, quiet: bool = False) -> None:
        """输出运行日志；quiet=True 时保持静默。"""
        if not quiet:
            print(message, flush=True)

    def now_iso(self) -> str:
        """返回当前 UTC 时间的 ISO 8601 字符串。"""
        return datetime.now(timezone.utc).isoformat()

    def sha256_bytes(self, data: bytes) -> str:
        """计算字节数据的 SHA-256 摘要。"""
        return hashlib.sha256(data).hexdigest()

    def cache_key(self, url: str) -> str:
        """根据资源 URL 生成稳定的缓存键。"""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def safe_json_dump(self, path: Path, data: Any) -> None:
        """通过临时文件原子写入 JSON，避免结果文件只写入一半。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def node_exe(self) -> Path:
        """查找配置指定或系统 PATH 中的 Node.js。"""
        return find_node()

    def load_fingerprint(self) -> Dict[str, Any]:
        """复制统一配置中的浏览器环境快照。"""
        return copy.deepcopy(config.FINGERPRINT)

    def normalize_user_agent(self, environment: Dict[str, Any]) -> str:
        """从指纹环境中提取 User-Agent，没有时使用默认值。"""
        nav = environment.get("navigator") if isinstance(environment.get("navigator"), dict) else {}
        return str(nav.get("userAgent") or DEFAULT_USER_AGENT)

    def normalize_language(self, environment: Dict[str, Any]) -> Tuple[str, List[str]]:
        """从指纹环境中提取 language 和 languages。"""
        nav = environment.get("navigator") if isinstance(environment.get("navigator"), dict) else {}
        language = str(nav.get("language") or "zh-CN")
        languages = (
            nav.get("languages") if isinstance(nav.get("languages"), list) else ["zh-CN", "zh"]
        )
        return (language, [str(item) for item in languages])

    def request_headers(
        self, user_agent: str, language: str, referer: Optional[str] = None
    ) -> Dict[str, str]:
        """构造页面及资源请求共用的 HTTP 请求头。"""
        base_lang = language.split("-")[0]
        headers = {
            "User-Agent": user_agent,
            "Accept-Language": f"{language},{base_lang};q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def cache_paths(self, url: str) -> Tuple[Path, Path]:
        """返回某个 URL 对应的缓存元数据和正文文件路径。"""
        key = self.cache_key(url)
        return (CACHE_DIR / f"{key}.json", CACHE_DIR / f"{key}.body")

    def decode_resource_body(self, raw: bytes, content_type: str, encoding: Optional[str]) -> str:
        """按照 Content-Type 和编码将资源字节转换成文本。"""
        ctype = (content_type or "").lower()
        if any(
            (
                marker in ctype
                for marker in ["text/", "javascript", "json", "xml", "svg", "html", "css"]
            )
        ):
            try:
                return raw.decode(encoding or "utf-8", errors="replace")
            except LookupError:
                return raw.decode("utf-8", errors="replace")
        return raw.decode("latin-1", errors="ignore")

    def fetch_resource(
        self,
        session: requests.Session,
        url: str,
        *,
        user_agent: str,
        language: str,
        referer: Optional[str] = None,
        cache: bool = True,
        refresh_cache: bool = False,
        quiet: bool = False,
        required: bool = True,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """优先从本地缓存读取资源；未命中时通过当前 HTTP 会话下载并缓存。"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        meta_path, body_path = self.cache_paths(url)
        if cache and (not refresh_cache) and meta_path.exists() and body_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                raw = body_path.read_bytes()
                body = self.decode_resource_body(
                    raw, str(meta.get("content_type") or ""), meta.get("encoding")
                )
                meta = dict(meta)
                meta["cache_state"] = "hit"
                meta["elapsed"] = 0.0
                return (
                    {
                        "url": url,
                        "status": int(meta.get("status", 200) or 200),
                        "headers": {"content-type": str(meta.get("content_type") or "")},
                        "body": body,
                    },
                    meta,
                )
            except Exception:
                pass
        start = time.perf_counter()
        try:
            resp = session.get(
                url,
                headers=self.request_headers(user_agent, language, referer),
                timeout=config.HTTP_TIMEOUT,
            )
            elapsed = time.perf_counter() - start
            raw = resp.content
            content_type = resp.headers.get("content-type", "")
            meta = {
                "url": url,
                "status": resp.status_code,
                "size": len(raw),
                "content_type": content_type,
                "encoding": resp.encoding or "",
                "sha256": self.sha256_bytes(raw),
                "fetch_time": self.now_iso(),
                "fetch_epoch": time.time(),
                "cache_state": "refresh" if refresh_cache else "miss",
                "elapsed": elapsed,
            }
            if cache and resp.status_code == 200:
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                body_path.write_bytes(raw)
            return (
                {
                    "url": url,
                    "status": resp.status_code,
                    "headers": {"content-type": content_type},
                    "body": self.decode_resource_body(raw, content_type, resp.encoding),
                },
                meta,
            )
        except Exception as exc:
            if required:
                raise
            self.log(f"[资源] 跳过 {url}: {exc}", quiet)
            return (None, {"url": url, "error": repr(exc), "cache_state": "error"})

    def parse_recaptcha_js_url(self, api_js: str) -> str:
        """从 api.js 中解析当前 release 的 recaptcha__*.js 地址。"""
        match = re.search("po\\.src=['\\\"]([^'\\\"]*recaptcha__[^'\\\"]+\\.js)['\\\"]", api_js)
        if match:
            return match.group(1)
        match = re.search(
            "https://www\\.gstatic\\.com/recaptcha/releases/[^'\\\"]+/recaptcha__[^'\\\"]+\\.js",
            api_js,
        )
        if match:
            return match.group(0)
        raise RuntimeError("api.js 中未找到 recaptcha__*.js URL")

    def recaptcha_version_from_url(self, url: str) -> str:
        """从 reCAPTCHA SDK URL 中提取 release 版本。"""
        match = re.search("/releases/([^/]+)/recaptcha__", url)
        return match.group(1) if match else ""

    def extract_attr_urls(self, html: str, base_url: str) -> List[str]:
        """提取 HTML 标签属性中的外部资源 URL。"""
        urls: List[str] = []
        for match in re.finditer("(?:src|href)\\s*=\\s*['\\\"]([^'\\\"]+)['\\\"]", html, re.I):
            value = match.group(1).strip()
            if value and (not value.startswith(("data:", "javascript:", "#"))):
                urls.append(urljoin(base_url, value))
        return urls

    def extract_css_urls(self, css: str, base_url: str) -> List[str]:
        """提取 CSS url(...) 中引用的资源 URL。"""
        urls: List[str] = []
        for match in re.finditer("url\\(\\s*(['\\\"]?)(?!data:)([^)'\\\"]+)\\1\\s*\\)", css, re.I):
            value = match.group(2).strip()
            if value and (not value.startswith(("data:", "#"))):
                urls.append(urljoin(base_url, value))
        return urls

    def unique_keep_order(self, items: Iterable[str]) -> List[str]:
        """去重列表，同时保持原始顺序。"""
        seen = set()
        out: List[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def collect_anchor_resources(
        self,
        session: requests.Session,
        anchor_url: str,
        *,
        user_agent: str,
        language: str,
        refresh_cache: bool,
        quiet: bool,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """获取本次 anchor iframe HTML 及其 CSS、JS、图片等依赖资源。"""
        start = time.perf_counter()
        resp = session.get(
            anchor_url,
            headers=self.request_headers(user_agent, language),
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        anchor_html = resp.text
        resources: List[Dict[str, Any]] = [
            {
                "url": anchor_url,
                "status": resp.status_code,
                "headers": {
                    "content-type": resp.headers.get("content-type", "text/html; charset=utf-8")
                },
                "body": anchor_html,
            }
        ]
        meta: Dict[str, Any] = {
            "anchor_length": len(anchor_html),
            "anchor_sha256": hashlib.sha256(resp.content).hexdigest(),
            "anchor_elapsed": time.perf_counter() - start,
            "static_resources": [],
        }
        attr_urls = [
            url
            for url in self.extract_attr_urls(anchor_html, anchor_url)
            if "gstatic.com/recaptcha" in url or "/recaptcha/" in url
        ]
        fetched_css: List[Tuple[str, str]] = []
        for url in self.unique_keep_order(attr_urls):
            resource, item_meta = self.fetch_resource(
                session,
                url,
                user_agent=user_agent,
                language=language,
                referer=anchor_url,
                cache=True,
                refresh_cache=refresh_cache,
                quiet=quiet,
                required=False,
            )
            if resource:
                resources.append(resource)
                ctype = str(resource.get("headers", {}).get("content-type", "")).lower()
                if "css" in ctype or url.endswith(".css"):
                    fetched_css.append((url, str(resource.get("body") or "")))
            if item_meta:
                meta["static_resources"].append(item_meta)
        css_urls: List[str] = []
        for css_url, css_body in fetched_css:
            css_urls.extend(self.extract_css_urls(css_body, css_url))
        for url in self.unique_keep_order(
            [u for u in css_urls if "gstatic.com/recaptcha" in u or "/recaptcha/" in u]
        ):
            resource, item_meta = self.fetch_resource(
                session,
                url,
                user_agent=user_agent,
                language=language,
                referer=anchor_url,
                cache=True,
                refresh_cache=refresh_cache,
                quiet=quiet,
                required=False,
            )
            if resource:
                resources.append(resource)
            if item_meta:
                meta["static_resources"].append(item_meta)
        return (resources, meta)

    def stderr_reader(self, pipe, quiet: bool, tail: List[str]) -> None:
        """持续读取 Node runner 的 stderr，并保留末尾日志用于诊断。"""
        try:
            for line in pipe:
                line = line.rstrip("\r\n")
                if line:
                    tail.append(line)
                    del tail[:-30]
                    if not quiet:
                        print(line, file=sys.stderr, flush=True)
        except Exception:
            pass

    def redact_url(self, url: str) -> str:
        """隐藏 URL 中较长的 token/c 参数，防止诊断输出泄漏完整令牌。"""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for key in ["token", "c"]:
            if key in qs and qs[key]:
                value = qs[key][0]
                if len(value) > 32:
                    qs[key] = [value[:16] + f"...len{len(value)}"]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(qs, doseq=True),
                parsed.fragment,
            )
        )

    def __init__(
        self,
        page_url: str,
        *,
        transport: str = "requests",
        refresh_cache: bool = False,
        quiet: bool = False,
        debug_encoder: bool = False,
        proxy: Optional[str] = None,
    ) -> None:
        """配置客户端并初始化指纹环境、HTTP 会话及运行选项。"""
        if transport not in {"requests", "curl-cffi"}:
            raise ValueError("transport 必须是 requests 或 curl-cffi")
        self.page_url = page_url
        self.transport = transport
        self.refresh_cache = bool(refresh_cache)
        self.quiet = bool(quiet)
        self.debug_encoder = bool(debug_encoder)
        self.proxy = (proxy or "").strip()
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.fingerprint = self.load_fingerprint()
        self.environment = (
            self.fingerprint.get("environment")
            if isinstance(self.fingerprint.get("environment"), dict)
            else {}
        )
        self.user_agent = self.normalize_user_agent(self.environment)
        self.language, self.languages = self.normalize_language(self.environment)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = config.VERIFY_TLS
        self._owns_session = True
        self.session.headers.update(self.request_headers(self.user_agent, self.language))
        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})
        self.last_resource_meta: Dict[str, Any] = {}
        self.last_mini_result: Dict[str, Any] = {}
        self.last_mini_process: Dict[str, Any] = {}
        self.last_reload_body = b""
        self.last_token = ""

    def _log(self, message: str) -> None:
        """输出当前客户端的日志，遵循 quiet 配置。"""
        self.log(message, self.quiet)
