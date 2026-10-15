"""真实功能调试辅助：保存逐阶段证据，并支持 IDE 中的可取消人工输入。"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from uuid import uuid4

from . import config
from .console import configure_console
from .process import hidden_process_flags


class DebugRun:
    """为一次真实功能运行创建独立结果目录，不改变业务返回值。"""

    def __init__(self, name: str):
        """创建带时间及随机编号的运行目录。"""
        configure_console()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.directory = config.OUTPUT_DIR / "debug" / name / f"{run_id}_{uuid4().hex[:8]}"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        self._lock = threading.Lock()
        print(f"本次运行目录：{self.directory.resolve()}", flush=True)

    def record(self, stage: str, data: dict) -> None:
        """保存真实响应、阶段数据和时间，控制台只显示关键状态。"""
        with self._lock:
            self._sequence += 1
            prefix = f"{self._sequence:03d}_{stage}"
            detail = dict(data)
            body = detail.pop("body", None)
            if body is not None:
                body_path = self.directory / f"{prefix}.html"
                body_path.write_bytes(body)
                detail["body_file"] = str(body_path.resolve())
                detail["body_bytes"] = len(body)
            detail["recorded_at"] = datetime.now(timezone.utc).isoformat()
            (self.directory / f"{prefix}.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"stage": stage, **detail}, ensure_ascii=False) + "\n")
            status = detail.get("status", detail.get("ok", ""))
            print(f"[{stage}] {status} {detail.get('url', '')}", flush=True)

    def finish(self, result: dict, *, print_full: bool = False) -> dict:
        """保存完整返回值，并输出可直接查看的实际结果摘要。"""
        path = self.directory / "result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        hidden = {
            "retry_text",
            "sg_ss",
            "cookies",
            "cookie_header",
            "protocol",
            "execution",
            "responseToken",
            "response_token",
            "protocol_token",
            "userverify_response_raw",
            "userverify_response",
            "userverifyRequest",
            "liveRequests",
            "networkEntries",
        }
        summary_keys = {
            "ok",
            "error",
            "engine",
            "first_status",
            "first_url",
            "first_kind",
            "challenge_executed",
            "retry_status",
            "retry_url",
            "retry_kind",
            "sg_ss_len",
            "source",
            "ms",
            "timings_ms",
            "total_ms",
            "protocol_completed",
            "response_token_length",
            "protocol_response_code",
            "protocol_has_next_challenge",
            "challenge_dir",
            "submit_status",
            "final_status",
            "final_url",
            "final_kind",
            "final_bytes",
            "html_file",
            "business_verified",
            "business_verification_status",
        }
        summary = (
            result
            if print_full
            else {
                key: value
                for key, value in result.items()
                if key in summary_keys and key not in hidden
            }
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(f"完整结果：{path.resolve()}", flush=True)
        return result

    def __enter__(self):
        """进入调试上下文。"""
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """发生异常时保存完整堆栈，并继续抛出以便 PyCharm 调试。"""
        if exc_value is not None:
            detail = {"ok": False, "error": str(exc_value), "type": exc_type.__name__}
            self.record("error", detail)
            (self.directory / "traceback.txt").write_text(
                "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
                encoding="utf-8",
            )
            self.finish(detail)
        return False


class IDEAnswerProvider:
    """使用可终止子进程读取 PyCharm 输入，避免退出时被 input 线程阻塞。"""

    def __init__(self, recorder: DebugRun, *, open_images: bool = False):
        """指定运行记录器，以及是否打开实际验证码图片。"""
        self.recorder = recorder
        self.open_images = open_images
        self._process = None
        self._lock = threading.Lock()
        self._closed = False

    def __call__(self, challenge: dict):
        """显示当前挑战，读取 IDE 控制台输入并交给 SDK 继续执行。"""
        self.recorder.record("challenge", challenge)
        print(f"\n第 {challenge.get('round')} 轮：{challenge.get('prompt', '')}", flush=True)
        print(f"图片：{challenge.get('image_path')}", flush=True)
        print(
            f"当前按钮：{challenge.get('verifyButtonText')}；提示：{challenge.get('errors')}",
            flush=True,
        )
        print("输入 1,4,7 选择图块；v=点击当前按钮；r=刷新；q=退出。", flush=True)
        if self.open_images and challenge.get("image_path"):
            from PIL import Image

            with Image.open(challenge["image_path"]) as image:
                image.show()
        # 子进程继承 IDE 的输入句柄，只有当前答案经标准输出回传。
        # 协议提前完成时可以终止子进程，不在主解释器留下阻塞 input 的线程。
        program = (
            "import json,sys; print('序号/命令> ',end='',file=sys.stderr,flush=True); "
            "answer=input(); print(json.dumps(answer,ensure_ascii=False),flush=True)"
        )
        with self._lock:
            if self._closed:
                return "q"
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-c", program],
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=hidden_process_flags(),
            )
            self._process = process
        output, _ = process.communicate()
        with self._lock:
            self._process = None
            if self._closed:
                return "q"
        if process.returncode != 0:
            raise EOFError("未收到输入；请使用 PyCharm 的 Run/Debug 控制台运行脚本。")
        answer = json.loads(output)
        self.recorder.record("answer", {"answer": answer})
        return answer

    def close(self) -> None:
        """终止仍等待用户输入的子进程。"""
        with self._lock:
            self._closed = True
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()

    def __enter__(self):
        """进入答案提供器上下文。"""
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """流程结束后取消尚未完成的用户输入。"""
        self.close()
