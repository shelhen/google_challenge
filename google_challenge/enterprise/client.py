"""Enterprise 协议客户端，提供人工交互与答案回调接口。"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode, urlparse

from .. import config
from ..config import (
    CHALLENGE_ROOT,
    DEFAULT_ORIGIN,
    DEFAULT_SITE_KEY,
    DETAIL_FILE,
    RESULT_DIR,
    RESULT_FILE,
    RUNNER_FILE,
    TEMP_DIR,
)
from ..process import hidden_process_flags
from .bridge import CancellableConsoleInput, InteractiveLineBridge
from .images import ChallengeImages
from .protocol import (
    EnterpriseUserVerifyError,
    normalize_result_evidence,
    parse_userverify_response,
)
from .resources import _EnterpriseBase

AnswerProvider = Callable[[Dict[str, Any]], Any]


class EnterpriseUserVerifyClient(ChallengeImages, _EnterpriseBase):
    """执行官方 SDK、转发网络并管理图片交互的协议客户端。"""

    def __init__(
        self,
        *,
        site_key: str = DEFAULT_SITE_KEY,
        origin: str = DEFAULT_ORIGIN,
        proxy: Optional[str] = config.PROXY,
        transport: str = config.ENTERPRISE_TRANSPORT,
        timeout: int = config.ENTERPRISE_TIMEOUT,
        challenge_dir: Optional[str | Path] = None,
        quiet: bool = config.ENTERPRISE_QUIET,
        refresh_cache: bool = config.ENTERPRISE_REFRESH_CACHE,
        debug_encoder: bool = config.ENTERPRISE_DEBUG_ENCODER,
        print_userverify_code1_response: bool = config.ENTERPRISE_PRINT_RESPONSE,
        session: Optional[Any] = None,
        challenge_s: Optional[str] = None,
        page_url: Optional[str] = None,
        user_agent: Optional[str] = None,
        language: Optional[str] = None,
        languages: Optional[List[str]] = None,
        environment: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化实例及运行参数。"""
        if transport not in {"requests", "curl-cffi"}:
            raise ValueError("Enterprise transport 必须是 requests 或 curl-cffi")
        self.print_userverify_code1_response = bool(print_userverify_code1_response)
        self.site_key = str(site_key or "").strip()
        if not self.site_key:
            raise ValueError("site_key 不能为空")
        self.origin = self.normalize_origin(origin)
        self.challenge_s = str(challenge_s or "").strip()
        self.protocol_timeout = max(30, int(timeout))
        configured_challenge_dir = Path(challenge_dir or CHALLENGE_ROOT).expanduser()
        self.challenge_root = (
            configured_challenge_dir
            if configured_challenge_dir.is_absolute()
            else config.PACKAGE_DIR / configured_challenge_dir
        )
        super().__init__(
            page_url=page_url or self.synthetic_page_url(self.origin),
            transport=transport,
            proxy=proxy,
            refresh_cache=refresh_cache,
            quiet=quiet,
            debug_encoder=debug_encoder,
        )
        if user_agent:
            self.user_agent = str(user_agent)
        if language:
            self.language = str(language)
        if languages:
            self.languages = [str(item) for item in languages]
        if isinstance(environment, dict):
            self.environment = self._merge_environment(self.environment, environment)
        if session is not None:
            self.session.close()
            self.session = session
            self._owns_session = False
        elif transport == "curl-cffi":
            self.session.close()
            try:
                from curl_cffi import requests as curl_requests
            except Exception as exc:
                raise RuntimeError("未安装 curl_cffi") from exc
            self.session = curl_requests.Session(
                impersonate=config.ENTERPRISE_IMPERSONATE,
                verify=config.VERIFY_TLS,
                trust_env=False,
            )
        if self._owns_session and self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})
        try:
            self.session.headers.update(self.request_headers(self.user_agent, self.language))
        except Exception:
            pass
        self.protocol_trace: List[Dict[str, Any]] = []
        self.current_bframe_url = ""
        self.last_reload_body = b""

    @staticmethod
    def _merge_environment(
        base: Optional[Dict[str, Any]], override: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """递归合并环境覆盖项，保留未覆盖的基线字段。"""
        output = dict(base or {})
        if not isinstance(override, dict):
            return output
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(output.get(key), dict):
                output[key] = EnterpriseUserVerifyClient._merge_environment(output[key], value)
            else:
                output[key] = value
        return output

    @staticmethod
    def normalize_origin(origin: str) -> str:
        """校验 HTTPS 来源，并只保留协议和主机部分。"""
        parsed = urlparse(str(origin or DEFAULT_ORIGIN))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("origin 必须是完整的 https origin")
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def synthetic_page_url(origin: str = DEFAULT_ORIGIN) -> str:
        """为独立协议运行构造与来源一致的页面地址。"""
        return (
            EnterpriseUserVerifyClient.normalize_origin(origin)
            + "/recaptcha-enterprise-protocol.html"
        )

    @staticmethod
    def build_userverify_url(
        site_key: str = DEFAULT_SITE_KEY, *, origin: str = DEFAULT_ORIGIN
    ) -> str:
        """根据站点密钥和来源构造验证接口地址。"""
        key = str(site_key or "").strip()
        if not key:
            raise ValueError("site_key 不能为空")
        return (
            EnterpriseUserVerifyClient.normalize_origin(origin)
            + "/recaptcha/enterprise/userverify?"
            + urlencode({"k": key})
        )

    parse_userverify_response = staticmethod(parse_userverify_response)
    normalize_result_evidence = staticmethod(normalize_result_evidence)

    def _bootstrap_html(self, api_url: str) -> str:
        """生成加载官方 SDK 和 Enterprise 组件的初始化页面。"""
        site_key = json.dumps(self.site_key)
        challenge_s = (
            f'            "s": {json.dumps(self.challenge_s)},\n' if self.challenge_s else ""
        )
        return f'<!doctype html>\n<html lang="{self.language}">\n<head>\n  <meta charset="utf-8">\n  <title>Enterprise protocol</title>\n  <script>\n    window.__enterpriseState = {{ phase: "loading", error: "" }};\n    window.__enterpriseLoaded = function () {{\n      try {{\n        window.__enterpriseWidgetId = grecaptcha.enterprise.render(\n          "captcha",\n          {{\n            sitekey: {site_key},\n{challenge_s}\n          }}\n        );\n        if (window.__enterpriseState.phase === "loading") window.__enterpriseState.phase = "ready";\n      }} catch (error) {{\n        window.__enterpriseState.phase = "error";\n        window.__enterpriseState.error = String(\n          error && error.message || error\n        );\n      }}\n    }};\n  </script>\n  <script src="{api_url}" async defer></script>\n</head>\n<body><div id="captcha"></div></body>\n</html>'

    def _prepare_bootstrap(self) -> tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
        """刷新 SDK 入口，读取版本化脚本并生成初始资源快照。"""
        api_url = (
            self.origin
            + "/recaptcha/enterprise.js?"
            + urlencode({"onload": "__enterpriseLoaded", "render": "explicit", "hl": self.language})
        )
        api_resource, api_meta = self.fetch_resource(
            self.session,
            api_url,
            user_agent=self.user_agent,
            language=self.language,
            referer=self.page_url,
            cache=True,
            refresh_cache=True,
            quiet=self.quiet,
        )
        if not api_resource or int(api_resource.get("status", 0)) != 200:
            raise EnterpriseUserVerifyError("enterprise.js 获取失败（需要当前入口脚本 HTTP 200）")
        release_url = self.parse_recaptcha_js_url(str(api_resource.get("body") or ""))
        release_resource, release_meta = self.fetch_resource(
            self.session,
            release_url,
            user_agent=self.user_agent,
            language=self.language,
            referer=api_url,
            cache=True,
            refresh_cache=self.refresh_cache,
            quiet=self.quiet,
        )
        if not release_resource or int(release_resource.get("status", 0)) != 200:
            raise EnterpriseUserVerifyError("Enterprise release JS 获取失败（需要 HTTP 200）")
        self._log("[enterprise] SDK release=" + self.recaptcha_version_from_url(release_url))
        api_resource = self._repair_utf8_resource(api_resource)
        release_resource = self._repair_utf8_resource(release_resource)
        return (
            self._bootstrap_html(api_url),
            [api_resource, release_resource],
            {
                "api_cache_policy": "refresh_unversioned_entry",
                "api": api_meta,
                "release": release_meta,
                "release_url": release_url,
            },
        )

    @staticmethod
    def _repair_utf8_resource(resource: Dict[str, Any]) -> Dict[str, Any]:
        """补齐文本资源的 UTF-8 字节信息。"""
        output = dict(resource)
        body = output.get("body")
        if not isinstance(body, str):
            return output
        try:
            repaired = body.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return output
        if repaired != body:
            output["body"] = repaired
        return output

    @staticmethod
    def _write_reply(process: subprocess.Popen, request_id: Any, **payload: Any) -> bool:
        """向 Node 标准输入写入一条带请求编号的 JSON 消息。"""
        if process.stdin is None:
            return False
        message = {"reply_to": str(request_id or "")}
        message.update(payload)
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def _frame_resources(self, url: str) -> List[Dict[str, Any]]:
        """获取目标框架文档及其静态资源。"""
        resources, _ = self.collect_anchor_resources(
            self.session,
            url,
            user_agent=self.user_agent,
            language=self.language,
            refresh_cache=self.refresh_cache,
            quiet=self.quiet,
        )
        return [self._repair_utf8_resource(resource) for resource in resources]

    def _payload_resources_from_response(
        self, response_text: str, *, referer: str
    ) -> List[Dict[str, Any]]:
        """提取响应中的图片标识并获取 SDK 需要的图片资源。"""
        raw = str(response_text or "").strip()
        if raw.startswith(")]}'"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[4:].strip()
        try:
            value = json.loads(raw)
        except Exception:
            return []
        tokens: List[str] = []

        def walk(item: Any) -> None:
            """递归遍历响应中的嵌套值。"""
            if isinstance(item, str):
                if item.startswith("06AF") and item not in tokens:
                    tokens.append(item)
                return
            if isinstance(item, list):
                for child in item:
                    walk(child)
            elif isinstance(item, dict):
                for child in item.values():
                    walk(child)

        walk(value)
        ids: List[str] = []
        if (
            isinstance(value, list)
            and value
            and (value[0] == "dresp")
            and (len(value) > 2)
            and isinstance(value[2], list)
        ):
            ids = [str(item) for item in value[2] if isinstance(item, (str, int)) and str(item)]
        resources: List[Dict[str, Any]] = []
        for token in tokens:
            variants: List[Optional[str]] = [image_id for image_id in ids] if ids else [None]
            for image_id in variants:
                query: Dict[str, str] = {"p": token, "k": self.site_key}
                if image_id is not None:
                    query["id"] = image_id
                url = self.origin + "/recaptcha/enterprise/payload?" + urlencode(query)
                headers = self.request_headers(self.user_agent, self.language, referer)
                headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                kwargs: Dict[str, Any] = {
                    "headers": headers,
                    "timeout": config.ENTERPRISE_PAYLOAD_TIMEOUT,
                }
                if self.proxy:
                    kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
                try:
                    image_started = time.perf_counter()
                    response = self.session.get(url, **kwargs)
                    self.protocol_trace.append(
                        {
                            "type": "payload",
                            "url": self.redact_url(url),
                            "image_id": image_id,
                            "status": int(response.status_code),
                            "response_length": len(response.content),
                            "elapsed": time.perf_counter() - image_started,
                        }
                    )
                    if response.status_code != 200:
                        continue
                    resources.append(
                        {
                            "url": str(response.url),
                            "status": int(response.status_code),
                            "headers": {
                                "content-type": str(
                                    response.headers.get("content-type", "image/jpeg")
                                )
                            },
                            "bodyBase64": base64.b64encode(bytes(response.content)).decode("ascii"),
                        }
                    )
                except Exception:
                    continue
        return resources

    def _print_userverify_code1_response(self, url: str, status: int, text: str) -> None:
        """显式开启调试时输出成功代码对应的原始响应。"""
        if not self.print_userverify_code1_response:
            return
        if urlparse(url).path not in {
            "/recaptcha/enterprise/userverify",
            "/recaptcha/api2/userverify",
        }:
            return
        try:
            parsed = self.parse_userverify_response(text)
        except EnterpriseUserVerifyError:
            return
        if not parsed["code_is_one"]:
            return
        print(
            f"\n[enterprise userverify code=1] HTTP {status}; full response body BEGIN\n"
            + text
            + "\n[enterprise userverify code=1] full response body END\n",
            flush=True,
        )

    def _forward_network_request(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """使用当前会话转发 SDK 网络请求并保留诊断记录。"""
        url = str(item.get("url") or "")
        method = str(item.get("method") or "GET").upper()
        body = base64.b64decode(str(item.get("bodyBase64") or ""))
        headers: Dict[str, str] = {}
        for name, value in (item.get("headers") or {}).items():
            normalized = str(name).lower()
            if normalized.startswith(":") or normalized in {
                "host",
                "authority",
                "content-length",
                "cookie",
                "connection",
                "accept-encoding",
            }:
                continue
            headers[str(name)] = str(value)
        headers.setdefault("Accept", "*/*")
        headers.setdefault("Accept-Language", f"{self.language},zh;q=0.9,en;q=0.8")
        headers.setdefault("User-Agent", self.user_agent)
        headers.setdefault("Origin", self.origin)
        endpoint_path = urlparse(url).path
        referer = (
            self.current_bframe_url
            if endpoint_path.startswith("/recaptcha/enterprise/") and self.current_bframe_url
            else str(item.get("locationHref") or item.get("referrer") or self.page_url)
        )
        headers["Referer"] = referer
        kwargs: Dict[str, Any] = {
            "headers": headers,
            "timeout": config.ENTERPRISE_FORWARD_TIMEOUT,
            "allow_redirects": True,
        }
        if method not in {"GET", "HEAD"}:
            kwargs["data"] = body
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        started = time.perf_counter()
        if endpoint_path.endswith("/reload"):
            self.last_reload_body = bytes(body)
        response = self.session.request(method, url, **kwargs)
        elapsed = time.perf_counter() - started
        response_body = bytes(response.content)
        self._print_userverify_code1_response(url, int(response.status_code), response.text)
        self._log(
            "[enterprise] "
            + method
            + " "
            + urlparse(url).path
            + f" body={len(body)} status={response.status_code}"
        )
        trace_item: Dict[str, Any] = {
            "type": "network",
            "method": method,
            "url": self.redact_url(url),
            "referer": self.redact_url(referer),
            "body_length": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "status": int(response.status_code),
            "response_length": len(response_body),
            "elapsed": elapsed,
        }
        if endpoint_path.endswith("/replaceimage") and len(response_body) <= 256:
            trace_item["response_text"] = response.text
        self.protocol_trace.append(trace_item)
        payload_resources = self._payload_resources_from_response(response.text, referer=referer)
        return {
            "status": int(response.status_code),
            "statusText": str(getattr(response, "reason", "") or ""),
            "url": str(response.url),
            "headers": {str(name): str(value) for name, value in response.headers.items()},
            "bodyBase64": base64.b64encode(response_body).decode("ascii"),
            "resources": payload_resources,
            "bridgeTotalSeconds": time.perf_counter() - started,
        }

    def run(self, answer_provider: Optional[AnswerProvider] = None) -> Dict[str, Any]:
        """运行验证码交互流程并返回完整协议结果。"""
        previous_bridge = getattr(self, "_answer_bridge", None)
        if previous_bridge is not None and previous_bridge.answer_running:
            raise EnterpriseUserVerifyError(
                "上一轮 answer_provider 尚未返回；请先结束旧输入，避免多轮争用控制台。"
            )
        use_console = answer_provider is None or answer_provider == self.console_answer_provider
        console_reader = CancellableConsoleInput() if use_console else None
        provider = (
            answer_provider
            if not use_console
            else lambda challenge: self.console_answer_provider(challenge, read_line=console_reader)
        )
        self.protocol_trace = []
        self.current_bframe_url = ""
        self.last_reload_body = b""
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.challenge_root.mkdir(parents=True, exist_ok=True)
        html, resources, resource_meta = self._prepare_bootstrap()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.challenge_root / f"{run_id}_{int(time.time() * 1000) % 1000000:06d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": "enterprise-userverify",
            "page_url": self.page_url,
            "html": html,
            "resources": resources,
            "site_key": self.site_key,
            "user_agent": self.user_agent,
            "language": self.language,
            "languages": self.languages,
            "environment": self.environment,
            "debug_encoder": self.debug_encoder,
            "protocol_timeout_ms": self.protocol_timeout * 1000,
            "network_timeout_ms": config.ENTERPRISE_NETWORK_TIMEOUT_MS,
            "answer_timeout_ms": config.ENTERPRISE_ANSWER_TIMEOUT_MS,
        }
        payload_handle = tempfile.NamedTemporaryFile(
            mode="w",
            prefix="enterprise_payload_",
            suffix=".json",
            dir=str(TEMP_DIR),
            encoding="utf-8",
            delete=False,
        )
        payload_file = Path(payload_handle.name)
        with payload_handle:
            json.dump(payload, payload_handle, ensure_ascii=False)
        stderr_tail: List[str] = []
        node = self.node_exe()
        process = subprocess.Popen(
            [str(node), str(RUNNER_FILE), str(payload_file)],
            cwd=str(config.PACKAGE_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=hidden_process_flags(),
        )
        stderr_thread = threading.Thread(
            target=self.stderr_reader, args=(process.stderr, self.quiet, stderr_tail), daemon=True
        )
        stderr_thread.start()
        result: Optional[Dict[str, Any]] = None
        started = time.perf_counter()
        assert process.stdout is not None
        bridge = InteractiveLineBridge(process.stdout)
        self._answer_bridge = bridge
        pending_challenge = None
        try:
            for event_kind, event in bridge:
                if event_kind == "interrupt":
                    if pending_challenge is None:
                        raise KeyboardInterrupt
                    request_id, _ = pending_challenge
                    self._write_reply(process, request_id, action="quit")
                    pending_challenge = None
                    continue
                if event_kind == "answer":
                    if pending_challenge is None:
                        continue
                    request_id, challenge = pending_challenge
                    pending_challenge = None
                    try:
                        if event["error"] is not None:
                            raise event["error"]
                        answer = self.normalize_answer(event["value"])
                        self.protocol_trace.append(
                            {
                                "type": "answer",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "answer_wait_seconds": event["wait_seconds"],
                                "controls_only": bool(challenge.get("controlsOnly")),
                                "verify_button_text": challenge.get("verifyButtonText"),
                                "verify_button_disabled": challenge.get("verifyButtonDisabled"),
                                "round": challenge.get("round"),
                                "action": answer.get("action"),
                                "indices": answer.get("indices") or [],
                                "image_path": challenge.get("image_path"),
                            }
                        )
                        self._write_reply(process, request_id, **answer)
                    except (EOFError, KeyboardInterrupt):
                        self._write_reply(process, request_id, action="quit")
                    except Exception as exc:
                        self._write_reply(process, request_id, action="quit", error=repr(exc))
                    continue
                line = event.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception as exc:
                    invalid_path = run_dir / "invalid_stdout_line.txt"
                    invalid_path.write_text(line, encoding="utf-8", errors="replace")
                    self.protocol_trace.append(
                        {
                            "type": "invalid-json",
                            "prefix": line[:300],
                            "suffix": line[-300:],
                            "length": len(line),
                            "error": repr(exc),
                            "path": str(invalid_path),
                        }
                    )
                    continue
                item_type = str(item.get("type") or "")
                request_id = item.get("id")
                if item_type == "need_frame":
                    try:
                        frame_url = str(item.get("url") or "")
                        if str(item.get("kind") or "") == "bframe":
                            self.current_bframe_url = frame_url
                        frame_resources = self._frame_resources(frame_url)
                        self._write_reply(process, request_id, resources=frame_resources)
                        self.protocol_trace.append(
                            {
                                "type": "frame",
                                "kind": item.get("kind"),
                                "url": self.redact_url(frame_url),
                                "resource_count": len(frame_resources),
                            }
                        )
                    except Exception as exc:
                        self._write_reply(process, request_id, error=repr(exc))
                elif item_type == "network_request":
                    self.protocol_trace.append(
                        {
                            "type": "network-dispatch",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "url": self.redact_url(str(item.get("url") or "")),
                            "answer_pending": pending_challenge is not None,
                        }
                    )
                    try:
                        response = self._forward_network_request(item)
                        self._write_reply(process, request_id, **response)
                    except Exception as exc:
                        self._write_reply(process, request_id, error=repr(exc))
                elif item_type == "challenge":
                    try:
                        if pending_challenge is not None:
                            raise EnterpriseUserVerifyError("Node emitted overlapping challenges")
                        challenge = self._save_challenge(dict(item.get("challenge") or {}), run_dir)
                        pending_challenge = (request_id, challenge)
                        bridge.request_answer(provider, challenge)
                    except Exception as exc:
                        self._write_reply(process, request_id, action="quit", error=repr(exc))
                elif item_type == "result":
                    value = item.get("result")
                    result = (
                        value
                        if isinstance(value, dict)
                        else {"ok": False, "error": "Node result 不是对象"}
                    )
                    break
            returncode = process.wait(timeout=20)
        finally:
            bridge.close()
            if console_reader is not None:
                console_reader.close()
                bridge.join_answer()
            payload_file.unlink(missing_ok=True)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            bridge.join_reader()
            stderr_thread.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if result is None:
            result = {"ok": False, "error": "Node runner did not emit result"}
        result["challenge_dir"] = str(run_dir)
        result["site_key"] = self.site_key
        result["userverify_url"] = self.build_userverify_url(self.site_key, origin=self.origin)
        result["transport"] = self.transport
        result["proxy"] = self.proxy or None
        result["process"] = {
            "returncode": returncode,
            "elapsed": time.perf_counter() - started,
            "node": str(node),
            "stderr_tail": stderr_tail[-30:],
        }
        result = self.normalize_result_evidence(result)
        detail = dict(result)
        detail["resource_meta"] = resource_meta
        detail["protocol_trace"] = self.protocol_trace
        detail["reload_request"] = {
            "body_length": len(self.last_reload_body),
            "body_sha256": hashlib.sha256(self.last_reload_body).hexdigest()
            if self.last_reload_body
            else "",
            "body_base64": base64.b64encode(self.last_reload_body).decode("ascii")
            if self.last_reload_body
            else "",
        }
        compact = dict(result)
        if isinstance(compact.get("userverifyRequest"), dict):
            request_summary = dict(compact["userverifyRequest"])
            request_summary.pop("bodyBase64", None)
            request_summary.pop("responseText", None)
            compact["userverifyRequest"] = request_summary
        compact.pop("userverify_response_raw", None)
        compact.pop("liveRequests", None)
        compact.pop("networkEntries", None)
        self.safe_json_dump(RESULT_FILE, compact)
        self.safe_json_dump(DETAIL_FILE, detail)
        return result

    def solve(self, answer_provider: Optional[AnswerProvider] = None) -> Dict[str, Any]:
        """运行验证码流程，可提供图片答案回调。"""
        return self.run(answer_provider=answer_provider)

    def send_userverify(
        self,
        body: bytes | bytearray | memoryview | str,
        *,
        referer: str,
        session: Optional[Any] = None,
        content_type: str = "application/x-www-form-urlencoded;charset=UTF-8",
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """提交指定验证请求正文并解析响应。"""
        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        if not body_bytes:
            raise ValueError("userverify body 不能为空")
        active_session = session if session is not None else self.session
        headers = {
            "Accept": "*/*",
            "Accept-Language": f"{self.language},zh;q=0.9,en;q=0.8",
            "Content-Type": content_type,
            "Origin": self.origin,
            "Referer": str(referer),
            "User-Agent": self.user_agent,
        }
        kwargs: Dict[str, Any] = {"data": body_bytes, "headers": headers, "timeout": timeout}
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        response = active_session.post(
            self.build_userverify_url(self.site_key, origin=self.origin), **kwargs
        )
        return {
            "status": int(response.status_code),
            "url": str(response.url),
            "response_length": len(response.content),
            "response_prefix": response.text[:500],
            "parsed": self.parse_userverify_response(response.text),
        }

    def close(self) -> None:
        """释放内部创建的会话；外部传入的会话由调用者管理。"""
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        """返回可在 with 语句中使用的客户端。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """退出上下文并释放内部会话。"""
        self.close()
