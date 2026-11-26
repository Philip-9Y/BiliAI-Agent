"""浏览器扩展本地桥。

扩展把当前 B 站页面信息 POST 到 127.0.0.1:8765/context：
标题、URL、播放进度、字幕、选中文字。App 用它替代只靠窗口标题猜视频。
只监听本机回环地址；不依赖公网。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOGGER = logging.getLogger("biliask.bridge")


def _fmt_time(seconds) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return ""
    return f"{total // 60:02d}:{total % 60:02d}"


class BrowserBridge:
    def __init__(self, port: int = 8765) -> None:
        self.port = int(port)
        self._latest: dict = {}
        self._updated_at = 0.0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> bool:
        if self._server is not None:
            return True
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if self.path.split("?")[0] != "/health":
                    self._json(404, {"ok": False})
                    return
                self._json(200, {"ok": True, "updated_at": bridge._updated_at})

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?")[0] != "/context":
                    self._json(404, {"ok": False})
                    return
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
                    raw = self.rfile.read(length)
                    data = json.loads(raw.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("context must be an object")
                except Exception:  # noqa: BLE001
                    self._json(400, {"ok": False, "error": "invalid json"})
                    return
                bridge.update(data)
                self._json(200, {"ok": True})

            def log_message(self, fmt: str, *args) -> None:
                return

        try:
            server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            server.daemon_threads = True
            self._server = server
            self.port = server.server_address[1]
            self._thread = threading.Thread(
                target=server.serve_forever, name="browser-bridge", daemon=True
            )
            self._thread.start()
            LOGGER.info("浏览器桥已启动: http://127.0.0.1:%s", self.port)
            return True
        except OSError:
            LOGGER.exception("浏览器桥启动失败，端口可能被占用: %s", self.port)
            self._server = None
            return False

    def update(self, data: dict) -> None:
        with self._lock:
            self._latest = dict(data)
            self._updated_at = time.time()

    def latest(self, max_age: float = 15.0) -> dict | None:
        with self._lock:
            if not self._latest or time.time() - self._updated_at > max_age:
                return None
            return dict(self._latest)

    def context_text(self, max_age: float = 15.0) -> str:
        data = self.latest(max_age)
        if not data:
            return ""
        title = str(data.get("title") or "").strip()
        url = str(data.get("url") or "").strip()
        subtitle = str(data.get("subtitle") or "").strip()
        selection = str(data.get("selection") or "").strip()
        current = _fmt_time(data.get("currentTime"))
        duration = _fmt_time(data.get("duration"))
        lines = ["【浏览器上下文（扩展上报）】"]
        if title:
            lines.append(f"页面标题：{title}")
        if url:
            lines.append(f"URL：{url}")
        if current:
            progress = f"播放进度：{current}"
            if duration:
                progress += f" / {duration}"
            lines.append(progress)
        if subtitle:
            lines.append(f"当前字幕：{subtitle[:1200]}")
        if selection:
            lines.append(f"选中文字：{selection[:1200]}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
