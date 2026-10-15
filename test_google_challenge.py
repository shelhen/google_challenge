"""直接运行的测试入口；修改下方全局变量，无需命令行参数。"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# 所有测试参数在这里修改。offline 仅使用本地 HTML 和模拟响应，不请求 Google。
TEST_MODE = "offline"  # offline、sgss、enterprise、business。
RUN_IV8_TESTS = True
RUN_CHROME_TESTS = True
ENGINE = "iv8"  # 在线 SG_SS 测试使用 iv8 或 chrome。
AUTO_START_CHROME = True
TARGET_URL = "https://www.google.com.hk/search?q=iphone&udm=2"
PROXY = None
SITE_KEY = None  # None 表示使用包内 config.py 默认值。
ORIGIN = "https://www.google.com"
CHALLENGE_S = None  # 真实挑战使用本次页面的 data-s，不能重复使用过期值。
PAGE_URL = None
TIMEOUT_SECONDS = 600
OUTPUT_HTML = Path(__file__).resolve().parent / "google_challenge/output/test_final.html"
TEST_VERBOSITY = 2
LOCAL_PAGE_URL = "https://example.test/probe"
LOCAL_HTML = "<!doctype html><script>document.cookie='SG_SS=local_probe; path=/';</script>"

from google_challenge import (  # noqa: E402
    ChromeService,
    GoogleSgssClient,
    config,  # noqa: E402
)
from google_challenge.console import configure_console  # noqa: E402
from google_challenge.enterprise.client import EnterpriseUserVerifyClient  # noqa: E402
from google_challenge.enterprise.protocol import (  # noqa: E402
    normalize_result_evidence,
    parse_userverify_response,
)
from google_challenge.process import find_node  # noqa: E402
from google_challenge.response import ResponseTools  # noqa: E402


class OfflineTests(unittest.TestCase):
    """验证协议边界、会话流程、图片和运行资源。"""

    def test_console_encoding(self):
        """从 GBK 输出流启动时，标准输出和错误输出均应转为 UTF-8。"""
        probe = """
import sys
sys.stdout.reconfigure(encoding='gbk')
sys.stderr.reconfigure(encoding='gbk')
from google_challenge.console import configure_console
configure_console()
print('\u4e2d\u6587\u8f93\u51fa')
print('\u4e2d\u6587\u9519\u8bef', file=sys.stderr)
"""
        result = subprocess.run([sys.executable, "-c", probe], check=True, capture_output=True)
        self.assertEqual(result.stdout.decode("utf-8").strip(), "中文输出")
        self.assertEqual(result.stderr.decode("utf-8").strip(), "中文错误")

    def test_response_classification(self):
        """HTTP 错误、SG 页面及 Enterprise 表单应分开判定。"""
        classify = ResponseTools.classify_response
        self.assertEqual(classify(500, LOCAL_PAGE_URL, "server error"), "http_error")
        self.assertEqual(classify(200, LOCAL_PAGE_URL, "SG_SS knitsail"), "sg_challenge")
        self.assertEqual(classify(200, LOCAL_PAGE_URL, "<html>正常页面</html>"), "ok")
        self.assertEqual(classify(429, "https://www.google.com/sorry/index", ""), "sorry_page")

    def test_form_context(self):
        """挑战绑定字段来自当前表单，支持相对地址及 HTML 转义。"""
        html = """<form id="captcha-form" action="index"><input name="q" value="a&amp;b">
        <input name="continue" value="https://example.test/search">
        <div class="g-recaptcha" data-sitekey="site" data-s="binding"></div></form>"""
        result = ResponseTools.extract_enterprise_context(html, "https://www.google.com/sorry/")
        self.assertEqual(result["site_key"], "site")
        self.assertEqual(result["challenge_s"], "binding")
        self.assertEqual(result["q"], "a&b")
        self.assertEqual(result["form_url"], "https://www.google.com/sorry/index")

    def test_cookie_domain_boundary(self):
        """相似域名不能匹配 Google Cookie 的父域。"""
        self.assertEqual(ResponseTools.cookie_domain("https://www.google.com"), ".google.com")
        self.assertEqual(ResponseTools.cookie_domain("https://evilgoogle.com"), "evilgoogle.com")

    def test_protocol_evidence(self):
        """仅最终响应有效，布尔值不能冒充整数成功代码。"""
        raw = ")]}'\n" + json.dumps(["uvresp", "local_token", 1])
        record = {
            "ok": True,
            "token_evidence_version": 3,
            "success_criterion": "userverify_response",
            "userverifyRequest": {"status": 200, "responseText": raw},
            "process": {"returncode": 0},
        }
        result = normalize_result_evidence(record)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["business_verified"])
        for value in (True, "1", 0):
            record["userverifyRequest"]["responseText"] = json.dumps(["uvresp", "token", value])
            self.assertFalse(normalize_result_evidence(record)["ok"])
        record["userverifyRequest"]["responseText"] = raw
        record["userverifyRequest"]["status"] = 500
        self.assertFalse(normalize_result_evidence(record)["ok"])

    def test_next_challenge_is_not_final(self):
        """存在后续图片挑战时不得将中间字符串当作最终 token。"""
        raw = json.dumps(["uvresp", "token", 1, None, None, None, None, ["rresp"]])
        self.assertTrue(parse_userverify_response(raw)["has_next_challenge"])
        result = normalize_result_evidence(
            {
                "ok": True,
                "token_evidence_version": 3,
                "success_criterion": "userverify_response",
                "userverifyRequest": {"status": 200, "responseText": raw},
            }
        )
        self.assertFalse(result["ok"])

    def test_image_and_external_session(self):
        """图片可在本地生成，客户端不应关闭外部传入的会话。"""
        from PIL import Image

        session = Mock(headers={})
        with tempfile.TemporaryDirectory() as folder:
            with EnterpriseUserVerifyClient(session=session, quiet=True) as client:
                buffer = io.BytesIO()
                Image.new("RGB", (20, 20), "red").save(buffer, format="PNG")
                challenge = {
                    "round": 1,
                    "rows": 1,
                    "columns": 1,
                    "tiles": [{"src": "local:image"}],
                    "imageURLs": ["local:image"],
                    "imageBodies": {"local:image": base64.b64encode(buffer.getvalue()).decode()},
                }
                saved = client._save_challenge(challenge, Path(folder))
                self.assertTrue(Path(saved["image_path"]).is_file())
                self.assertFalse(saved["download_errors"])
                with Image.open(saved["image_path"]) as image:
                    self.assertEqual(image.getpixel((90, 90)), (255, 0, 0))
                self.assertEqual(
                    client.normalize_answer("1,3"), {"action": "select", "indices": [1, 3]}
                )
            session.close.assert_not_called()

    def test_business_submission_uses_current_form(self):
        """业务提交使用本次隐藏字段及同一会话，保存跳转后的实际正文。"""
        from google_challenge import GoogleChallengeFlow

        html = """<form id="captcha-form" action="index"><input name="q" value="current-q">
        <input name="continue" value="https://www.google.com/search?q=test">
        <div class="g-recaptcha" data-sitekey="site" data-s="current-binding"></div></form>"""
        session = Mock(headers={"User-Agent": config.DEFAULT_USER_AGENT})
        session.get.side_effect = [
            Mock(status_code=429, url="https://www.google.com/sorry/index", text=html),
            Mock(
                status_code=200,
                url="https://www.google.com/search?q=test",
                text="local final",
                content=b"local final",
            ),
        ]
        session.post.return_value = Mock(
            status_code=302,
            url="https://www.google.com/sorry/index",
            headers={"Location": "/search?q=test"},
        )
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("google_challenge.flow.requests.Session", return_value=session),
            patch("google_challenge.flow.EnterpriseUserVerifyClient") as client_class,
        ):
            client_class.return_value.__enter__.return_value.solve.return_value = {
                "ok": True,
                "response_token": "local-token",
            }
            with GoogleChallengeFlow() as flow:
                result = flow.run(LOCAL_PAGE_URL, output_file=Path(folder) / "final.html")
            self.assertEqual(Path(result["html_file"]).read_bytes(), b"local final")
            self.assertEqual(session.post.call_args.kwargs["data"]["q"], "current-q")
            self.assertEqual(client_class.call_args.kwargs["challenge_s"], "current-binding")
            self.assertIs(client_class.call_args.kwargs["session"], session)
            self.assertEqual(result["final_kind"], "ok")
            self.assertIsNone(result["business_verified"])

    def test_windows_console_input_and_cancel(self):
        """模拟 Windows 控制台按键，校验退格、提交与取消。"""
        from google_challenge.enterprise.bridge import CancellableConsoleInput

        keys = iter(["1", "2", "\b", "3", "\r"])
        console = Mock()
        console.kbhit.return_value = True
        console.getwch.side_effect = lambda: next(keys)
        with (
            patch("google_challenge.enterprise.bridge.sys.platform", "win32"),
            patch.dict(sys.modules, {"msvcrt": console}),
            patch("builtins.print"),
        ):
            reader = CancellableConsoleInput(Mock(isatty=lambda: True))
            self.assertEqual(reader(""), "13")
            reader.close()
            with self.assertRaises(EOFError):
                reader("")

    def test_resource_cache_without_network(self):
        """相同资源二次读取应命中包内缓存，不再次请求网络。"""
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("google_challenge.enterprise.resources.CACHE_DIR", Path(folder)),
            EnterpriseUserVerifyClient(quiet=True) as client,
        ):
            session = Mock()
            session.get.return_value = Mock(
                status_code=200,
                content=b"local-resource",
                text="local-resource",
                headers={"Content-Type": "application/javascript"},
                encoding="utf-8",
            )
            kwargs = {"user_agent": "local", "language": "zh-CN", "quiet": True}
            first, _ = client.fetch_resource(session, LOCAL_PAGE_URL, **kwargs)
            second, _ = client.fetch_resource(session, LOCAL_PAGE_URL, **kwargs)
            self.assertEqual(first["body"], second["body"])
            self.assertEqual(session.get.call_count, 1)

    def test_sgss_retry_and_cookie_forwarding(self):
        """统一请求链必须传递初始 Cookie，并拒绝将 HTTP 500 判为成功。"""
        import requests

        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        jar = requests.cookies.RequestsCookieJar()
        jar.set("seed", "local", domain="example.test", path="/")
        session.cookies.jar = jar
        session.cookies.get_dict.return_value = {"seed": "local"}
        session.get.side_effect = [
            Mock(status_code=200, url=LOCAL_PAGE_URL, text="SG_SS knitsail; var eid='eid';"),
            Mock(status_code=500, url=LOCAL_PAGE_URL, text="server error"),
        ]
        client = GoogleSgssClient(engine="chrome")
        with (
            patch("curl_cffi.requests.Session", return_value=session),
            patch.object(client, "execute", return_value={"ok": True, "sg_ss": "local"}) as execute,
        ):
            result = client.get_cookie(LOCAL_PAGE_URL, pass_initial_cookies=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["retry_kind"], "http_error")
        self.assertEqual(execute.call_args.kwargs["cookies"][0]["name"], "seed")
        self.assertEqual(session.get.call_count, 2)

    def test_javascript_runtime(self):
        """JavaScript 资源语法有效，浏览器核心可独立加载。"""
        node = str(find_node())
        for script in config.RUNTIME_DIR.glob("*.js"):
            subprocess.run([node, "--check", str(script)], check=True, capture_output=True)
        result = subprocess.run(
            [
                node,
                "-e",
                "console.log(typeof require(process.argv[1]).installBrowserEnv)",
                str(config.RUNTIME_DIR / "browser_core.js"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("function", result.stdout)
        probe = """
        const proc = process;
        const core = require(proc.argv[1]);
        core.installBrowserEnv({location: {href: 'https://example.test/'}});
        const element = document.createElement('div');
        element.textContent = 'local-dom';
        proc.stdout.write(JSON.stringify({tag: element.tagName, text: element.textContent}));
        proc.exit(0);
        """
        result = subprocess.run(
            [node, "-e", probe, str(config.RUNTIME_DIR / "browser_core.js")],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), {"tag": "DIV", "text": "local-dom"})
        evidence_probe = """
        const evidence = require(process.argv[1]);
        const response = {status: 200, completedAtMs: 1};
        evidence.recordEnterpriseProtocolResponse(response, JSON.stringify(['uvresp', 'local', 1]));
        const first = evidence.enterpriseProtocolOutcome({userverifyRequests: [response]});
        response.status = 500;
        const second = evidence.enterpriseProtocolOutcome({userverifyRequests: [response]});
        process.stdout.write(JSON.stringify([first.ok, second.ok]));
        """
        result = subprocess.run(
            [node, "-e", evidence_probe, str(config.RUNTIME_DIR / "enterprise_evidence.js")],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), [True, False])

    @unittest.skipUnless(RUN_IV8_TESTS, "已关闭 IV8 测试")
    def test_iv8_local_cookie(self):
        """真实 IV8 执行本地 HTML，随后新上下文不应继承 Cookie。"""
        client = GoogleSgssClient(engine="iv8")
        self.assertEqual(client.get_sg_ss(LOCAL_HTML, LOCAL_PAGE_URL), "local_probe")
        result = client.execute("<!doctype html><p>空页面</p>", LOCAL_PAGE_URL, timeout_ms=100)
        self.assertFalse(result["ok"])

    @unittest.skipUnless(RUN_CHROME_TESTS, "已关闭 Chrome 测试")
    def test_chrome_isolation_and_cleanup(self):
        """真实 Chrome 复用进程，同时隔离 Cookie 并清理任务上下文。"""
        with ChromeService() as service:
            engine = service.engine
            initial = engine.health()
            first = engine.generate(LOCAL_HTML, LOCAL_PAGE_URL)
            self.assertEqual(first["sg_ss"], "local_probe")
            html = (
                "<script>document.cookie='SG_SS='+document.cookie.includes('seed=local');</script>"
            )
            second = engine.generate(
                html, LOCAL_PAGE_URL, cookies=[{"name": "seed", "value": "local"}]
            )
            self.assertEqual(second["sg_ss"], "true")
            third = engine.generate(html, LOCAL_PAGE_URL)
            self.assertEqual(third["sg_ss"], "false")
            final = engine.health()
            self.assertEqual(initial["browser_pid"], final["browser_pid"])
            self.assertEqual(final["active_contexts"], 0)
            process = service.process
        self.assertIsNotNone(process.poll())


def print_summary(result: dict) -> None:
    """打印精简结果，避免将完整 token 和页面正文写到控制台。"""
    hidden = {"retry_text", "sg_ss", "cookies", "cookie_header", "protocol", "execution"}
    summary = {key: value for key, value in result.items() if key not in hidden}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    """按文件顶部的测试模式执行本地检查或单次在线流程。"""
    configure_console()
    if TEST_MODE == "offline":
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineTests)
        result = unittest.TextTestRunner(verbosity=TEST_VERBOSITY).run(suite)
        if not result.wasSuccessful():
            raise SystemExit(1)
    elif TEST_MODE == "sgss":
        client = GoogleSgssClient(engine=ENGINE)
        if ENGINE == "chrome" and AUTO_START_CHROME:
            with ChromeService():
                result = client.get_cookie(TARGET_URL, proxy=PROXY)
        else:
            result = client.get_cookie(TARGET_URL, proxy=PROXY)
        print_summary(result)
    elif TEST_MODE == "enterprise":
        with EnterpriseUserVerifyClient(
            site_key=SITE_KEY or config.DEFAULT_SITE_KEY,
            origin=ORIGIN,
            challenge_s=CHALLENGE_S,
            page_url=PAGE_URL,
            proxy=PROXY,
            timeout=TIMEOUT_SECONDS,
        ) as client:
            result = client.solve()
        print_summary(
            {
                key: result.get(key)
                for key in (
                    "ok",
                    "error",
                    "protocol_completed",
                    "response_token_length",
                    "business_verified",
                )
            }
        )
    elif TEST_MODE == "business":
        from google_challenge import GoogleChallengeFlow

        with GoogleChallengeFlow(proxy=PROXY) as flow:
            print_summary(flow.run(TARGET_URL, output_file=OUTPUT_HTML))
    else:
        raise ValueError("TEST_MODE 必须是 offline、sgss、enterprise 或 business。")


if __name__ == "__main__":
    main()
