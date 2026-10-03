"""Local Ollama proxy: serialize inference and prioritize interactive requests."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UPSTREAM = os.getenv("TENAZAS_OLLAMA_UPSTREAM_URL", "http://192.168.2.6:11434").rstrip("/")
CONDITION = threading.Condition()
ACTIVE = 0
WAITING_INTERACTIVE = 0


class InferenceSlot:
    def __init__(self, interactive=False, timeout=30):
        self.interactive = interactive
        self.timeout = timeout

    def __enter__(self):
        global ACTIVE, WAITING_INTERACTIVE
        deadline = time.monotonic() + self.timeout
        with CONDITION:
            if self.interactive:
                WAITING_INTERACTIVE += 1
            try:
                while ACTIVE or (not self.interactive and WAITING_INTERACTIVE):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("inference queue deadline exceeded")
                    CONDITION.wait(remaining)
                ACTIVE += 1
            finally:
                if self.interactive:
                    WAITING_INTERACTIVE -= 1
                CONDITION.notify_all()
        return self

    def __exit__(self, *_):
        global ACTIVE
        with CONDITION:
            ACTIVE -= 1
            CONDITION.notify_all()


class Handler(BaseHTTPRequestHandler):
    def _error(self, status, message):
        body = json.dumps({"error": message}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, body=None):
        request = Request(
            UPSTREAM + self.path,
            data=body,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
            method=self.command,
        )
        try:
            response = urlopen(request, timeout=120)
        except HTTPError as exc:
            response = exc
        except (URLError, OSError):
            self._error(503, "GPU backend unavailable")
            return
        with response:
            self.send_response(response.status)
            self.send_header(
                "Content-Type", response.headers.get("Content-Type", "application/json")
            )
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while chunk := response.read1(8192):
                self.wfile.write(chunk)
                self.wfile.flush()

    def do_GET(self):
        if self.path == "/health":
            self._error(200, "inference gate running")
        elif self.path in ("/api/tags", "/api/ps", "/api/version", "/v1/models"):
            self._proxy()
        else:
            self._error(404, "unsupported endpoint")

    def do_POST(self):
        if self.path not in (
            "/api/generate",
            "/api/chat",
            "/api/embed",
            "/api/embeddings",
            "/api/show",
            "/v1/chat/completions",
            "/v1/embeddings",
        ):
            self._error(404, "unsupported endpoint")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(400, "invalid content length")
            return
        if length <= 0 or length > 16 * 1024 * 1024:
            self._error(413, "request too large or empty")
            return
        self.connection.settimeout(30)
        body = self.rfile.read(length)
        if self.path == "/api/show":
            self._proxy(body)
            return
        try:
            interactive = self.headers.get("X-Tenazas-Priority") == "interactive"
            with InferenceSlot(interactive):
                self._proxy(body)
        except TimeoutError:
            self._error(503, "GPU busy; retry later")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18902), Handler).serve_forever()
