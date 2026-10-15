"""管理子进程消息、异步答案及可取消的跨平台控制台输入。"""

from __future__ import annotations

import os
import queue
import select
import sys
import threading
import time


class InteractiveLineBridge:
    """以线程安全消息连接 Node 输出和人工答案。"""

    def __init__(self, stdout):
        """初始化实例及运行参数。"""
        self._events = queue.Queue()
        self._closed = threading.Event()
        self._pending = False
        self._answer_thread = None
        self._reader = threading.Thread(
            target=self._read, args=(stdout,), name="enterprise-stdout", daemon=True
        )
        self._reader.start()

    @property
    def answer_pending(self):
        """判断是否有尚未完成的答案请求。"""
        return self._pending

    @property
    def answer_running(self):
        """判断答案回调线程是否仍在执行。"""
        return self._answer_thread is not None and self._answer_thread.is_alive()

    def _put(self, kind, value):
        """桥接未关闭时将消息放入线程安全队列。"""
        if not self._closed.is_set():
            self._events.put((kind, value))

    def _read(self, stdout):
        """读取 Node 输出行，并转交异常和结束事件。"""
        try:
            for line in stdout:
                if self._closed.is_set():
                    break
                self._put("line", line)
        except Exception as exc:
            self._put("reader_error", exc)
        finally:
            self._put("eof", None)

    def request_answer(self, provider, challenge):
        """启动单个答案回调任务，将结果交还主线程。"""
        if self._closed.is_set() or self._pending:
            raise RuntimeError("An answer is already pending or the bridge is closed")
        self._pending = True

        def work():
            """执行答案回调，并记录等待时间和异常。"""
            started = time.perf_counter()
            try:
                value = provider(challenge)
                event = {"value": value, "error": None}
            except BaseException as exc:
                event = {"value": None, "error": exc}
            event["wait_seconds"] = time.perf_counter() - started
            self._put("answer", event)

        self._answer_thread = threading.Thread(target=work, name="enterprise-answer", daemon=True)
        self._answer_thread.start()

    def __iter__(self):
        """持续产出进程输出、答案或中断事件。"""
        while not self._closed.is_set():
            try:
                kind, value = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                yield ("interrupt", None)
                continue
            if kind == "answer":
                self._pending = False
            if kind == "eof":
                return
            if kind == "reader_error":
                raise value
            yield (kind, value)

    def close(self):
        """关闭实例持有的资源。"""
        self._closed.set()

    def join_answer(self, timeout=1):
        """有界等待答案线程退出。"""
        if self._answer_thread is not None:
            self._answer_thread.join(timeout=timeout)

    def join_reader(self):
        """子进程结束后有界等待输出读取线程退出。"""
        self._reader.join(timeout=2)


class CancellableConsoleInput:
    """支持 Windows 和 POSIX 的可取消控制台读取器。"""

    def __init__(self, stream=None):
        """初始化实例及运行参数。"""
        stream = sys.stdin if stream is None else stream
        self._windows = sys.platform == "win32"
        self._closed = threading.Event()
        self._text_buffer = ""
        if self._windows:
            if not stream.isatty():
                raise RuntimeError("Windows 交互需要系统终端；自动化调用请传入 answer_provider。")
            return
        try:
            self._fd = stream.fileno()
            select.select([self._fd], [], [], 0)
        except (AttributeError, OSError, ValueError) as exc:
            raise RuntimeError(
                "控制台不支持可取消的文件描述符读取；请在系统终端运行入口，或提供可自行结束、且不阻塞 input() 的 answer_provider。"
            ) from exc
        self._encoding = getattr(stream, "encoding", None) or "utf-8"
        self._buffer = bytearray()
        self._closed = threading.Event()
        self._eof = False

    def __call__(self, prompt):
        """读取一行用户输入，同时允许调用者取消等待。"""
        print(prompt, end="", flush=True)
        if self._windows:
            return self._read_windows()
        while not self._closed.is_set():
            if b"\n" in self._buffer:
                line, _, rest = self._buffer.partition(b"\n")
                self._buffer = bytearray(rest)
                return bytes(line).decode(self._encoding, errors="replace").rstrip("\r")
            if self._eof:
                if self._buffer:
                    line, self._buffer = (self._buffer, bytearray())
                    return bytes(line).decode(self._encoding, errors="replace")
                raise EOFError
            ready, _, _ = select.select([self._fd], [], [], 0.1)
            if ready:
                data = os.read(self._fd, 4096)
                if data:
                    self._buffer.extend(data)
                else:
                    self._eof = True
        raise EOFError("Console input cancelled")

    def _read_windows(self):
        """使用 Windows 控制台接口逐字读取，允许主线程随时取消。"""
        import msvcrt

        while not self._closed.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            character = msvcrt.getwch()
            if character in {"\x00", "\xe0"}:
                msvcrt.getwch()
            elif character in {"\r", "\n"}:
                print(flush=True)
                answer, self._text_buffer = self._text_buffer, ""
                return answer
            elif character == "\x03":
                raise KeyboardInterrupt
            elif character == "\x1a":
                raise EOFError
            elif character == "\b":
                if self._text_buffer:
                    self._text_buffer = self._text_buffer[:-1]
                    print("\b \b", end="", flush=True)
            else:
                self._text_buffer += character
                print(character, end="", flush=True)
        raise EOFError("控制台输入已取消。")

    def close(self):
        """关闭实例持有的资源。"""
        self._closed.set()
