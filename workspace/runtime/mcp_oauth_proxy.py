"""Hardened repo integration of the sanitized staging OAuth proxy.

No runtime configuration, certificates or secrets are supplied by this module.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import re
import ssl
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import jwt

from workspace.runtime.mcp_oauth_config import REQUIRED_SCOPE, ProxyConfig, https_url
from workspace.runtime.mcp_readonly_adapter import _open_snapshot

AUDIT = logging.getLogger("tenazas.oauth.access")
PUBLIC_METHODS = frozenset(
    {"initialize", "notifications/initialized", "ping", "server/discover", "tools/list"}
)
APPLICATION_WRITE_SCOPE = "write:applications"
APPLICATION_WRITE_TOOLS = frozenset({"register_job_application", "update_job_application", "resolve_job_mail_suggestion", "delete_job_application"})


class ValidatorUnavailable(Exception):
    pass


class InsufficientScope(Exception):
    pass


class UpstreamUnavailable(Exception):
    pass


def load_internal_bearer(config):
    if os.name != "posix":
        raise ValueError("POSIX credential policy required")
    path = Path(config.internal_bearer_file).absolute()
    if path.resolve(strict=True) != path:
        raise ValueError("Unsafe credential layout")
    before = path.lstat()
    with _open_snapshot(path) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) not in (0o400, 0o600, 0o640)
            or (info.st_uid, info.st_gid) != (config.bearer_uid, config.bearer_gid)
            or info.st_size > 256
            or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError("Unsafe credential file")
        raw = stream.read(257)
    if len(raw) > 256:
        raise ValueError("Credential size limit")
    token = raw.decode("ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{32,128}", token):
        raise ValueError("Invalid internal credential")
    return token


class HTTPSClient:
    """Exact URL allowlist, verified TLS, no redirects/proxies or client headers."""

    def __init__(self, config, context=None):
        self.config = config
        self.context = context or ssl.create_default_context()

    def request(self, url, *, body=None, token=None, jwks=False):
        https_url(url)
        allowed = {self.config.jwks_url} if jwks else set(self.config.allowed_upstreams)
        if url not in allowed:
            raise UpstreamUnavailable()
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=self.config.connect_timeout,
            context=self.context,
        )
        try:
            connection.connect()
            connection.sock.settimeout(self.config.read_timeout)
            headers = {"Accept": "application/json", "Connection": "close"}
            if not jwks:
                headers.update(
                    {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
                )
            connection.request(
                "GET" if jwks else "POST", parsed.path or "/", body=body, headers=headers
            )
            response = connection.getresponse()
            if (jwks and response.status != 200) or (
                not jwks and response.status not in (200, 202)
            ):
                # Includes every redirect; never request the Location destination.
                raise UpstreamUnavailable()
            limit = 65536 if jwks else self.config.response_limit
            sizes = response.headers.get_all("Content-Length", [])
            if len(sizes) > 1 or (sizes and (not sizes[0].isdigit() or int(sizes[0]) > limit)):
                raise UpstreamUnavailable()
            data = response.read(limit + 1)
            if len(data) > limit or (sizes and len(data) != int(sizes[0])):
                raise UpstreamUnavailable()
            return response.status, data
        except (OSError, http.client.HTTPException, ValueError, TypeError) as exc:
            raise UpstreamUnavailable() from exc
        finally:
            connection.close()


class AccessValidator:
    """Bounded JWKS cache. Unknown kid cannot trigger per-request JWKS fetches."""

    def __init__(self, config, fetch_jwks):
        self.config = config
        self.fetch_jwks = fetch_jwks
        self._keys = None
        self._expires = 0
        self._lock = threading.Lock()

    def _signing_key(self, kid):
        with self._lock:
            if self._keys is None or time.monotonic() >= self._expires:
                try:
                    document = self.fetch_jwks()
                    if (
                        not isinstance(document, dict)
                        or not isinstance(document.get("keys"), list)
                        or not 1 <= len(document["keys"]) <= 64
                    ):
                        raise ValueError()
                    keys = {}
                    for item in document["keys"]:
                        if not isinstance(item, dict):
                            raise ValueError()
                        if (
                            item.get("kty") != "RSA"
                            or item.get("alg", "RS256") != "RS256"
                            or item.get("use", "sig") != "sig"
                        ):
                            continue
                        identity = item.get("kid")
                        if (
                            not isinstance(identity, str)
                            or not identity
                            or len(identity) > 256
                            or identity in keys
                            or "d" in item
                        ):
                            raise ValueError()
                        if "key_ops" in item and item["key_ops"] != ["verify"]:
                            continue
                        keys[identity] = jwt.PyJWK.from_dict(item, algorithm="RS256").key
                    if not keys:
                        raise ValueError()
                    self._keys = keys
                    self._expires = time.monotonic() + self.config.jwks_ttl
                except Exception as exc:
                    raise ValidatorUnavailable() from exc
            if kid not in self._keys:
                raise jwt.InvalidTokenError()
            return self._keys[kid]

    def validate(self, token):
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid or len(kid) > 256:
            raise jwt.InvalidTokenError()
        claims = jwt.decode(
            token,
            self._signing_key(kid),
            algorithms=["RS256"],
            audience=self.config.audience,
            issuer=self.config.issuer,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
        scope = claims.get("scope")
        if not isinstance(scope, str):
            raise jwt.InvalidTokenError()
        if REQUIRED_SCOPE not in scope.split():
            raise InsufficientScope()
        return claims


class OAuthProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        config,
        *,
        validator=None,
        upstream=None,
        bearer_reader=load_internal_bearer,
        tls_context=None,
    ):
        self.config = config
        client = HTTPSClient(config)
        self.upstream = upstream or (
            lambda body, token: client.request(config.upstream_mcp, body=body, token=token)
        )
        self.validator = validator or AccessValidator(
            config, lambda: json.loads(client.request(config.jwks_url, jwks=True)[1])
        )
        self.bearer_reader = bearer_reader
        self.tls_context = tls_context
        self._slots = threading.BoundedSemaphore(config.concurrency)
        self._rate_lock = threading.Lock()
        self._start = time.monotonic()
        self._requests = 0
        super().__init__(address, Handler)

    def permitted(self):
        with self._rate_lock:
            if time.monotonic() - self._start >= self.config.rate_window:
                self._requests = 0
                self._start = time.monotonic()
            self._requests += 1
            return self._requests <= self.config.request_rate

    def handle_error(self, request, client_address):
        AUDIT.warning("transport_error")

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
            request.settimeout(self.config.client_timeout)
            if self.tls_context is not None:
                try:
                    request = self.tls_context.wrap_socket(request, server_side=True)
                except (OSError, ssl.SSLError):
                    self.shutdown_request(request)
                    return
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TenazasOAuthProxy"
    sys_version = ""

    def log_message(self, *_args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {"error": "invalid HTTP request"})

    def reply(self, status, payload=None, *, raw=None, challenge=None, required_scope=REQUIRED_SCOPE):
        body = raw if raw is not None else json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if challenge:
            self.send_header(
                "WWW-Authenticate",
                f'Bearer resource_metadata="{self.server.config.resource_metadata}", scope="{required_scope}", error="{challenge}"',
            )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True
        AUDIT.info("request status=%d", status)

    def boundary(self):
        if not self.server.permitted():
            self.reply(429, {"error": "rate limit"})
            return False
        if self.headers.get_all("Origin"):
            self.reply(403, {"error": "origin forbidden"})
            return False
        if len(self.headers.get_all("Authorization", [])) > 1:
            self.reply(400, {"error": "invalid authorization framing"})
            return False
        return True

    def handle_expect_100(self):
        self.reply(417, {"error": "expectation forbidden"})
        return False

    def do_POST(self):
        if not self.boundary():
            return
        if self.path != "/mcp":
            return self.reply(404, {"error": "not found"})
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get_all("Transfer-Encoding")
            or len(lengths) != 1
            or not re.fullmatch(r"[0-9]{1,10}", lengths[0])
        ):
            return self.reply(400, {"error": "invalid framing"})
        length = int(lengths[0])
        if not 0 < length <= self.server.config.request_limit:
            return self.reply(413, {"error": "body limit"})
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or types[0].strip().lower() not in {
            "application/json",
            "application/json; charset=utf-8",
            "application/json;charset=utf-8",
        }:
            return self.reply(415, {"error": "content type"})
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            request = json.loads(body)
            if (
                not isinstance(request, dict)
                or request.get("jsonrpc") != "2.0"
                or not isinstance(request.get("method"), str)
            ):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError, OSError):
            return self.reply(400, {"error": "invalid request"})
        if request["method"] not in PUBLIC_METHODS:
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                return self.reply(401, {"error": "missing bearer"}, challenge="invalid_token")
            token = authorization[7:].strip()
            try:
                claims = self.server.validator.validate(token)
                if request["method"] == "tools/call" and isinstance(request.get("params"), dict) and request["params"].get("name") in APPLICATION_WRITE_TOOLS:
                    if APPLICATION_WRITE_SCOPE not in str(claims.get("scope", "")).split():
                        return self.reply(403, {"error": "application write permission required"}, challenge="insufficient_scope", required_scope=APPLICATION_WRITE_SCOPE)
            except InsufficientScope:
                return self.reply(
                    403, {"error": "insufficient scope"}, challenge="insufficient_scope"
                )
            except jwt.InvalidTokenError:
                return self.reply(401, {"error": "invalid token"}, challenge="invalid_token")
            except Exception:
                return self.reply(503, {"error": "validator unavailable"})
        try:
            internal = self.server.bearer_reader(self.server.config)
            status, data = self.server.upstream(body, internal)
            if status not in (200, 202) or len(data) > self.server.config.response_limit:
                raise UpstreamUnavailable()
            self.reply(status, raw=data)
        except Exception:
            self.reply(502, {"error": "upstream unavailable"})

    def do_GET(self):
        if not self.boundary():
            return
        if self.path == "/.well-known/oauth-protected-resource/mcp":
            return self.reply(
                200,
                {
                    "resource": self.server.config.audience,
                    "authorization_servers": [self.server.config.issuer],
                    "scopes_supported": [REQUIRED_SCOPE, APPLICATION_WRITE_SCOPE],
                },
            )
        self.reply(405, {"error": "method forbidden"})

    def do_HEAD(self):
        self.do_GET()

    def forbidden_method(self):
        self.reply(405, {"error": "method forbidden"})

    def do_PUT(self):
        self.forbidden_method()

    def do_DELETE(self):
        self.forbidden_method()

    def do_PATCH(self):
        self.forbidden_method()

    def do_OPTIONS(self):
        self.forbidden_method()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", type=Path, required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--port", type=int, default=8445)
    args = parser.parse_args()
    config = ProxyConfig(**json.loads(args.config_file.read_text()))
    load_internal_bearer(config)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    OAuthProxy(("127.0.0.1", args.port), config, tls_context=context).serve_forever()


if __name__ == "__main__":
    main()
