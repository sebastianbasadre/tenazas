import json
import ssl
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from jwt import PyJWKClient

from config import (
    ISSUER,
    AUDIENCE,
    REQUIRED_SCOPE,
    JWKS_URL,
    UPSTREAM_MCP,
    INTERNAL_BEARER_FILE,
)

JWKS = PyJWKClient(JWKS_URL)

def load_internal_bearer():
    with open(INTERNAL_BEARER_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

def validate_access_token(token):
    signing_key = JWKS.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "iss", "aud"]},
    )
    scopes = set(str(claims.get("scope", "")).split())
    if REQUIRED_SCOPE not in scopes:
        raise jwt.InvalidTokenError("required scope missing")
    return claims

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TenazasOAuthProxy/0.1"

    def log_message(self, *_args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header(
                "WWW-Authenticate",
                'Bearer resource_metadata="https://localhost:8445/.well-known/oauth-protected-resource/mcp", scope="read:tenazas", error="invalid_token"'
            )
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/mcp":
            return self.reply(404, {"error": "not found"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16384:
                return self.reply(413, {"error": "body limit"})

            body = self.rfile.read(length)
            request = json.loads(body)

            if not isinstance(request, dict):
                return self.reply(400, {"error": "invalid request"})

            method = request.get("method")
        except Exception:
            return self.reply(400, {"error": "invalid request"})

        public_methods = {
            "initialize",
            "notifications/initialized",
            "ping",
            "server/discover",
            "tools/list",
        }

        if method not in public_methods:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return self.reply(401, {"error": "missing bearer"})

            token = auth[7:].strip()

            try:
                validate_access_token(token)
            except Exception:
                return self.reply(401, {"error": "invalid token"})

        try:
            req = urllib.request.Request(
                UPSTREAM_MCP,
                data=body,
                method="POST",
                headers={
                    "Authorization": "Bearer " + load_internal_bearer(),
                    "Content-Type": "application/json",
                },
            )

            ctx = ssl.create_default_context()

            with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
                upstream_body = r.read()
                self.send_response(r.status)
                self.send_header(
                    "Content-Type",
                    r.headers.get("Content-Type", "application/json")
                )
                self.send_header("Content-Length", str(len(upstream_body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(upstream_body)

        except Exception:
            self.reply(502, {"error": "upstream unavailable"})

    def do_GET(self):
        if self.path == "/.well-known/oauth-protected-resource/mcp":
            return self.reply(200, {
                "resource": AUDIENCE,
                "authorization_servers": [ISSUER],
                "scopes_supported": [REQUIRED_SCOPE],
            })
        self.reply(405, {"error": "method forbidden"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_GET

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8445), Handler)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(
        "/opt/tenazas-oauth-proxy/tls/proxy.crt",
        "/opt/tenazas-oauth-proxy/tls/proxy.key",
    )

    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
