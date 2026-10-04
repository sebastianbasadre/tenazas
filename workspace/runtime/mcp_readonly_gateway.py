"""Authenticated, stateless JSON-RPC MCP test transport. No production startup hook."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import ssl
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workspace.runtime import mcp_readonly_adapter as adapter
from workspace.runtime import mcp_cache_policy

MAX_BODY = 16384
PROTOCOL = "2025-03-26"
AUDIT = logging.getLogger("tenazas.mcp.access")


def load_token(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError("Credential file must be a private regular file")
    with adapter._open_snapshot(path.absolute()) as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_size > 256 or opened.st_mode & 0o077 or opened.st_nlink != 1:
            raise ValueError("Invalid credential file")
        token = stream.read(257).decode("ascii").strip()
    if not 32 <= len(token) <= 128 or not token.isalnum():
        raise ValueError("Credential must contain 32-128 ASCII alphanumeric characters")
    return token


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token_file, *, limit=120, window=60):
        self.token_file = Path(token_file)
        load_token(self.token_file)
        self.limit, self.window = limit, window
        self.tls_context = None
        self._lock = threading.Lock()
        self._requests = 0
        self._slots = threading.BoundedSemaphore(16)
        self._start = time.monotonic()
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            if self.tls_context is not None:
                request.settimeout(5)
                try:
                    request = self.tls_context.wrap_socket(request, server_side=True)
                except (OSError, ssl.SSLError):
                    self.shutdown_request(request)
                    return
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def permitted(self):
        with self._lock:
            now = time.monotonic()
            if now - self._start >= self.window:
                self._requests, self._start = 0, now
            self._requests += 1
            return self._requests <= self.limit


class Handler(BaseHTTPRequestHandler):
    server_version = "TenazasMCP"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass  # Never log request lines, headers, credentials or user-controlled text.

    def send_error(self, code, message=None, explain=None):
        # The stdlib default reflects malformed request lines and unsupported verbs.
        self.reply(code, {"error": "invalid HTTP request"})

    def reply(self, status, payload=None):
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="tenazas-mcp"')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True
        AUDIT.info("request status=%d", status)

    def authenticated(self):
        if not self.server.permitted():
            self.reply(429, {"error": "rate limit"})
            return False
        headers = self.headers.get_all("Authorization", [])
        try:
            expected = "Bearer " + load_token(self.server.token_file)
        except (OSError, ValueError, UnicodeError):
            self.reply(503, {"error": "unavailable"})
            return False
        if len(headers) != 1 or not hmac.compare_digest(headers[0].encode(), expected.encode()):
            self.reply(401, {"error": "unauthorized"})
            return False
        return True

    def do_POST(self):
        if not self.authenticated():
            return
        if self.path != "/mcp":
            return self.reply(404, {"error": "not found"})
        if self.headers.get("Origin") is not None:
            return self.reply(403, {"error": "browser origin forbidden"})
        if self.headers.get_all("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            return self.reply(400, {"error": "invalid framing"})
        try:
            length = int(self.headers["Content-Length"])
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {"error": "body limit"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.reply(415, {"error": "content type"})
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            request = json.loads(raw)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError()
            identity = request.get("id")
            if identity is not None and (type(identity) not in (str, int) or
                                         len(str(identity)) > 64):
                raise ValueError()
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return self.reply(400, {"error": "invalid request"})
        # Never echo request identifiers: they can contain secrets. MCP clients
        # must use bounded numeric request IDs on this deliberately narrow transport.
        if type(identity) is not int and identity is not None:
            return self.reply(400, {"error": "numeric id required"})
        if method == "notifications/initialized" and identity is None and not params:
            return self.reply(202)
        if identity is None:
            return self.reply(400, {"error": "request id required"})
        error = None
        if method == "initialize":
            if params.get("protocolVersion") != PROTOCOL:
                error = {"code": -32602, "message": "Unsupported protocol"}
            result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "tenazas-readonly-test", "version": "0.1"}}
        elif method == "ping" and not params:
            result = {}
        elif method == "tools/list" and not params:
            result = {"tools": [{"name": name, "description": "Read sanitized cached observability",
                                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                                 "annotations": {"readOnlyHint": True, "destructiveHint": False}}
                                for name in adapter.TOOLS]}
        elif method == "tools/call":
            if set(params) - {"name", "arguments"} or params.get("arguments", {}) != {} or not isinstance(params.get("name"), str) or params.get("name") not in adapter.TOOLS:
                error = {"code": -32602, "message": "Tool or arguments forbidden"}
            else:
                try:
                    mcp_cache_policy.validate_cache(adapter.CACHE_ROOT)
                    value = adapter.call_tool(params["name"])
                    result = {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}], "isError": False}
                except Exception:
                    error = {"code": -32603, "message": "Read unavailable"}
        else:
            error = {"code": -32601, "message": "Method forbidden"}
        return self.reply(200, {"jsonrpc": "2.0", "id": identity,
                                **({"error": error} if error else {"result": result})})

    def do_GET(self):
        if self.authenticated():
            self.reply(405, {"error": "method forbidden"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_GET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args()
    if not args.cert or not args.key:
        parser.error("TLS certificate and key required")
    mcp_cache_policy.validate_cache(adapter.CACHE_ROOT)
    server = Gateway(("127.0.0.1", args.port), args.token_file)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    server.tls_context = context
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    server.serve_forever()


if __name__ == "__main__":
    main()
