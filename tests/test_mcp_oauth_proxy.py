"""OAuth hardening tests with generated keys, local JWKS and loopback only."""

import dataclasses
import http.client
import ipaddress
import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from tests.test_mcp_snapshot_producer import snapshot
from workspace.runtime import mcp_oauth_proxy as proxy
from workspace.runtime.mcp_oauth_config import ProxyConfig

INTERNAL = "I" * 48
META = "/.well-known/oauth-protected-resource/mcp"


@pytest.fixture
def config(tmp_path):
    return ProxyConfig(
        issuer="https://issuer.fixture.invalid/",
        audience="https://resource.fixture.invalid",
        jwks_url="https://issuer.fixture.invalid/jwks",
        upstream_mcp="https://upstream.fixture.invalid/mcp",
        allowed_upstreams=("https://upstream.fixture.invalid/mcp",),
        resource_metadata="https://resource.fixture.invalid" + META,
        internal_bearer_file=tmp_path / "bearer",
        bearer_uid=os.geteuid() if os.name == "posix" else 1000,
        bearer_gid=os.getegid() if os.name == "posix" else 1000,
        request_rate=1000,
    )


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="fixture-key", alg="RS256", use="sig")
    return key, other, {"keys": [public]}


def encode(config, key, changes=None, *, omit=(), kid="fixture-key", algorithm="RS256"):
    now = int(time.time())
    claims = {
        "iss": config.issuer,
        "aud": config.audience,
        "exp": now + 300,
        "iat": now - 1,
        "scope": "read:tenazas",
    }
    claims.update(changes or {})
    for name in omit:
        claims.pop(name)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


@pytest.fixture
def validator(config, keys):
    return proxy.AccessValidator(config, lambda: keys[2])


def test_valid_token_and_exact_scope(config, keys, validator):
    assert validator.validate(encode(config, keys[0]))["scope"] == "read:tenazas"
    assert validator.validate(encode(config, keys[0], {"scope": "other read:tenazas"}))


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://wrong.fixture.invalid/"},
        {"aud": "wrong"},
        {"exp": 1},
        {"iat": int(time.time()) + 86400},
        {"scope": [" read:tenazas "]},
        {"scope": {"x": " read:tenazas "}},
        {"scope": None},
    ],
)
def test_invalid_claims(config, keys, validator, change):
    with pytest.raises(jwt.InvalidTokenError):
        validator.validate(encode(config, keys[0], change))


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "scope"])
def test_required_claims(config, keys, validator, claim):
    with pytest.raises(jwt.InvalidTokenError):
        validator.validate(encode(config, keys[0], omit=(claim,)))


@pytest.mark.parametrize("algorithm", ["RS512", "HS256", "none"])
def test_disallowed_algorithms(config, keys, validator, algorithm):
    key = (
        keys[0]
        if algorithm == "RS512"
        else "fixture-secret-for-tests-only-123456789"
        if algorithm == "HS256"
        else None
    )
    with pytest.raises(jwt.InvalidTokenError):
        validator.validate(encode(config, key, algorithm=algorithm))


def test_bad_signature_malformed_unknown_kid(config, keys, validator):
    for token in ("not-a-token", encode(config, keys[1]), encode(config, keys[0], kid="unknown")):
        with pytest.raises(jwt.InvalidTokenError):
            validator.validate(token)


@pytest.mark.parametrize(
    "scope", ["", "read:tenazas-more", "read:tenazas:write", "READ:TENAZAS", "other"]
)
def test_insufficient_scope(config, keys, validator, scope):
    with pytest.raises(proxy.InsufficientScope):
        validator.validate(encode(config, keys[0], {"scope": scope}))


def test_validator_unavailable_and_cached_unknown_kid(config, keys):
    calls = []

    def fetch():
        calls.append(1)
        return keys[2]

    validator = proxy.AccessValidator(config, fetch)
    for _ in range(3):
        with pytest.raises(jwt.InvalidTokenError):
            validator.validate(encode(config, keys[0], kid="unknown"))
    assert len(calls) == 1

    def fail():
        raise OSError("private-validator-detail")

    with pytest.raises(proxy.ValidatorUnavailable):
        proxy.AccessValidator(config, fail).validate(encode(config, keys[0]))


@pytest.fixture
def running(config, validator):
    captured = []

    def upstream(body, token):
        captured.append((body, token))
        return 200, b'{"fixture":true}'

    server = proxy.OAuthProxy(
        ("127.0.0.1", 0),
        config,
        validator=validator,
        upstream=upstream,
        bearer_reader=lambda _: INTERNAL,
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()

    def request(payload=None, *, method="POST", path="/mcp", auth=None, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        outgoing = {"Content-Type": "application/json"}
        if auth is not None:
            outgoing["Authorization"] = auth
        outgoing.update(headers or {})
        connection.request(
            method, path, json.dumps(payload) if payload is not None else None, outgoing
        )
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    yield server, request, captured
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def rpc(method="tools/call", identity="fixture", params=None):
    return {
        "jsonrpc": "2.0",
        "id": identity,
        "method": method,
        "params": params or {"name": "get_status", "arguments": {}},
    }


def test_401_403_challenges_and_503(running, config, keys):
    server, request, captured = running
    for auth in (
        None,
        "Bearer malformed",
        "Bearer " + encode(config, keys[0], {"exp": 1}),
        "Bearer " + encode(config, keys[0], {"scope": [" read:tenazas "]}),
        "Bearer " + encode(config, keys[0], {"scope": {"x": " read:tenazas "}}),
    ):
        status, headers, _ = request(rpc(), auth=auth)
        assert status == 401 and 'error="invalid_token"' in headers["WWW-Authenticate"]
        assert config.resource_metadata in headers["WWW-Authenticate"]
        assert "localhost" not in headers["WWW-Authenticate"]
    status, headers, _ = request(
        rpc(), auth="Bearer " + encode(config, keys[0], {"scope": "other"})
    )
    assert status == 403 and 'error="insufficient_scope"' in headers["WWW-Authenticate"]

    class Unavailable:
        def validate(self, _):
            raise proxy.ValidatorUnavailable("private-detail")

    server.validator = Unavailable()
    assert request(rpc(), auth="Bearer anything")[0] == 503
    assert not captured


def test_public_allowlist_and_no_auth_bypass(running):
    _, request, captured = running
    assert {
        "initialize",
        "notifications/initialized",
        "ping",
        "server/discover",
        "tools/list",
    } == proxy.PUBLIC_METHODS
    for name in proxy.PUBLIC_METHODS:
        assert request(rpc(name))[0] == 200
    for name in ("tools/call", "resources/read", "tools/call "):
        assert request(rpc(name))[0] == 401
    assert len(captured) == 5


def test_jwt_not_forwarded_and_body_ids_meta_preserved(running, config, keys):
    _, request, captured = running
    token = encode(config, keys[0])
    for identity in ("fixture", 123):
        payload = rpc(
            identity=identity,
            params={"name": "get_status", "arguments": {}, "_meta": {"fixture": True}},
        )
        assert (
            request(
                payload,
                auth="Bearer " + token,
                headers={"Cookie": "client-private", "X-Private": "client-private"},
            )[0]
            == 200
        )
        body, internal = captured[-1]
        assert internal == INTERNAL and token.encode() not in body
        assert json.loads(body) == payload


@pytest.mark.parametrize("method", [[], {}, None, 2, True])
def test_non_string_jsonrpc_method(running, method):
    assert running[1](rpc(method))[0] == 400
    assert not running[2]


def test_origins_content_type_body_and_upstream_failures(running, config, keys):
    server, request, captured = running
    # Header gates run before body consumption. Header-only requests avoid the
    # Windows close/reset race with unread bytes without changing proxy policy.
    assert request(headers={"Origin": "https://client.fixture.invalid"})[0] == 403
    assert request(headers={"Content-Type": "text/plain", "Content-Length": "1"})[0] == 415
    assert request(None, headers={"Content-Length": str(config.request_limit + 1)})[0] == 413
    assert not captured
    token = "Bearer " + encode(config, keys[0])

    def timeout(*_):
        raise TimeoutError("private-upstream-detail")

    server.upstream = timeout
    status, _, body = request(rpc(), auth=token)
    assert status == 502 and b"private" not in body
    server.upstream = lambda *_: (200, b"x" * (config.response_limit + 1))
    assert request(rpc(), auth=token)[0] == 502


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_methods_forbidden_on_metadata(running, method):
    assert running[1](method=method, path=META)[0] == 405


def test_metadata_get_head_and_http11(running):
    _, request, _ = running
    assert request(method="GET", path=META)[0] == 200
    status, headers, body = request(method="HEAD", path=META)
    assert status == 200 and body == b"" and int(headers["Content-Length"]) > 0
    assert headers["Connection"] == "close"
    assert request(method="GET", path="/mcp")[0] == 405
    assert proxy.Handler.protocol_version == "HTTP/1.1"


def raw_request(server, headers, *, body=b""):
    connection = socket.create_connection(server.server_address, timeout=3)
    request = b"POST /mcp HTTP/1.1\r\nHost: fixture\r\n" + headers + b"\r\n" + body
    connection.sendall(request)
    response = http.client.HTTPResponse(connection)
    response.begin()
    status = response.status
    response.read()
    connection.close()
    return status


@pytest.mark.parametrize(
    "headers",
    [
        b"Transfer-Encoding: chunked\r\nContent-Length: 1\r\n",
        b"Content-Length: 1\r\nContent-Length: 2\r\n",
        b"Content-Length: -1\r\n",
        b"Content-Length: nope\r\n",
        b"Content-Length: 0\r\nAuthorization: Bearer a\r\nAuthorization: Bearer b\r\n",
    ],
)
def test_ambiguous_framing_and_auth_duplicate(running, headers):
    assert raw_request(running[0], headers) == 400
    assert not running[2]


def test_safe_logging(running, config, keys, caplog):
    _, request, _ = running
    token = encode(config, keys[0])
    with caplog.at_level(logging.INFO):
        request(rpc(), auth="Bearer " + token)
        request(rpc("client-private-method"), auth="Bearer client-private-token")
        request(method="GET", path="/private?token=client-private-query")
    assert token not in caplog.text and INTERNAL not in caplog.text
    assert "client-private" not in caplog.text and "Authorization" not in caplog.text


@pytest.fixture
def tls_backend(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    state = {"status": 200, "body": b"{}", "location": None, "delay": 0, "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["seen"].append((dict(self.headers), body))
            time.sleep(state["delay"])
            self.send_response(state["status"])
            if state["location"]:
                self.send_header("Location", state["location"])
            if state.get("send_length", True):
                self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            with suppress(OSError):
                self.wfile.write(state["body"])

        def do_GET(self):
            self.do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    state["server_context"] = context
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield (
        f"https://127.0.0.1:{server.server_port}/mcp",
        ssl.create_default_context(cafile=str(cert_file)),
        state,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location", ["https://other.fixture.invalid/mcp", "http://127.0.0.1:1/mcp"]
)
def test_redirects_never_followed(config, tls_backend, status, location):
    url, context, state = tls_backend
    state.update(status=status, location=location)
    config = dataclasses.replace(config, upstream_mcp=url, allowed_upstreams=(url,))
    with pytest.raises(proxy.UpstreamUnavailable):
        proxy.HTTPSClient(config, context).request(url, body=b"{}", token=INTERNAL)
    assert len(state["seen"]) == 1


def test_https_client_limits_timeout_and_only_internal_headers(config, tls_backend):
    url, context, state = tls_backend
    config = dataclasses.replace(
        config, upstream_mcp=url, allowed_upstreams=(url,), response_limit=8, read_timeout=0.05
    )
    client = proxy.HTTPSClient(config, context)
    assert client.request(url, body=b"{}", token=INTERNAL) == (200, b"{}")
    headers, body = state["seen"][-1]
    assert headers["Authorization"] == "Bearer " + INTERNAL
    assert set(headers) == {
        "Host",
        "Accept-Encoding",
        "Content-Length",
        "Accept",
        "Connection",
        "Authorization",
        "Content-Type",
    }
    assert body == b"{}"
    state["body"] = b"x" * 9
    with pytest.raises(proxy.UpstreamUnavailable):
        client.request(url, body=b"{}", token=INTERNAL)
    state.update(body=b"{}", delay=0.15)
    with pytest.raises(proxy.UpstreamUnavailable):
        client.request(url, body=b"{}", token=INTERNAL)


def test_https_response_limit_without_content_length(config, tls_backend):
    url, context, state = tls_backend
    config = dataclasses.replace(
        config, upstream_mcp=url, allowed_upstreams=(url,), response_limit=8
    )
    state.update(body=b"x" * 9, send_length=False)
    with pytest.raises(proxy.UpstreamUnavailable):
        proxy.HTTPSClient(config, context).request(url, body=b"{}", token=INTERNAL)


def test_local_https_jwks_fetch(config, tls_backend, keys):
    url, context, state = tls_backend
    config = dataclasses.replace(config, jwks_url=url)
    state["body"] = json.dumps(keys[2]).encode()
    client = proxy.HTTPSClient(config, context)
    validator = proxy.AccessValidator(config, lambda: json.loads(client.request(url, jwks=True)[1]))
    assert validator.validate(encode(config, keys[0]))["scope"] == "read:tenazas"
    assert "Authorization" not in state["seen"][0][0]
    assert state["seen"][0][1] == b""


def test_tls_handshake_timeout_and_proxy_https(running, tls_backend):
    server = running[0]
    _, client_context, state = tls_backend
    server.config = dataclasses.replace(server.config, client_timeout=0.1)
    server.tls_context = state["server_context"]
    stalled = socket.create_connection(server.server_address, timeout=2)
    stalled.settimeout(2)
    assert stalled.recv(1) == b""
    stalled.close()
    client = http.client.HTTPSConnection(*server.server_address, context=client_context, timeout=2)
    client.request("GET", META)
    response = client.getresponse()
    assert response.status == 200 and response.version == 11
    response.read()
    client.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://upstream.fixture.invalid/mcp",
        "https://other.fixture.invalid/mcp",
        "https://user:secret@upstream.fixture.invalid/mcp",
    ],
)
def test_upstream_config_allowlist(config, url):
    with pytest.raises(ValueError):
        dataclasses.replace(config, upstream_mcp=url)


def test_upstream_connect_timeout(config, monkeypatch):
    seen = []

    class Connection:
        def __init__(self, host, port, **options):
            seen.append(options["timeout"])

        def connect(self):
            raise TimeoutError("private-connection-detail")

        def close(self):
            pass

    monkeypatch.setattr(proxy.http.client, "HTTPSConnection", Connection)
    with pytest.raises(proxy.UpstreamUnavailable):
        proxy.HTTPSClient(config).request(config.upstream_mcp, body=b"{}", token=INTERNAL)
    assert seen == [config.connect_timeout]


@pytest.mark.linux_filesystem
@pytest.mark.skipif(
    sys.platform != "linux", reason="Requires Linux credential owner/mode/link semantics"
)
@pytest.mark.parametrize(
    "violation",
    [
        None,
        "symlink",
        "parent_symlink",
        "hardlink",
        "perms",
        "size",
        "empty",
        "owner",
        "group",
        "directory",
    ],
)
def test_internal_bearer_file(config, tmp_path, violation):
    path = config.internal_bearer_file
    path.write_text(INTERNAL)
    path.chmod(0o600)
    if violation == "symlink":
        path.rename(tmp_path / "target")
        path.symlink_to(tmp_path / "target")
    elif violation == "parent_symlink":
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        config = dataclasses.replace(config, internal_bearer_file=tmp_path / "alias" / "bearer")
    elif violation == "hardlink":
        os.link(path, tmp_path / "link")
    elif violation == "perms":
        path.chmod(0o644)
    elif violation == "size":
        path.write_text("X" * 257)
    elif violation == "empty":
        path.write_text("")
    elif violation == "owner":
        config = dataclasses.replace(config, bearer_uid=os.geteuid() + 1)
    elif violation == "group":
        config = dataclasses.replace(config, bearer_gid=os.getegid() + 1)
    elif violation == "directory":
        path.unlink()
        path.mkdir()
    if violation is None:
        assert proxy.load_internal_bearer(config) == INTERNAL
    else:
        with pytest.raises((ValueError, OSError)):
            proxy.load_internal_bearer(config)


@pytest.mark.linux_filesystem
@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux file mode semantics")
@pytest.mark.parametrize("mode", [0o400, 0o600, 0o640])
def test_internal_bearer_allowed_modes(config, mode):
    path = config.internal_bearer_file
    path.write_text(INTERNAL)
    path.chmod(mode)
    assert proxy.load_internal_bearer(config) == INTERNAL


@pytest.mark.linux_filesystem
@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux UID/GID semantics")
@pytest.mark.parametrize("identity", ["bearer_uid", "bearer_gid"])
def test_internal_bearer_0640_wrong_identity(config, identity):
    path = config.internal_bearer_file
    path.write_text(INTERNAL)
    path.chmod(0o640)
    wrong = dataclasses.replace(config, **{identity: getattr(config, identity) + 1})
    with pytest.raises(ValueError, match="Unsafe credential file"):
        proxy.load_internal_bearer(wrong)


@pytest.mark.linux_filesystem
@pytest.mark.skipif(
    sys.platform != "linux", reason="Requires Linux file mode and special bit semantics"
)
@pytest.mark.parametrize("mode", [0o620, 0o660, 0o644, 0o641, 0o670, 0o4640, 0o2640, 0o1640])
def test_internal_bearer_forbidden_modes(config, mode):
    path = config.internal_bearer_file
    path.write_text(INTERNAL)
    path.chmod(mode)
    with pytest.raises(ValueError, match="Unsafe credential file"):
        proxy.load_internal_bearer(config)


def test_client_read_timeout_and_incomplete_body(running):
    server, _, captured = running
    server.config = dataclasses.replace(server.config, client_timeout=0.1)
    connection = socket.create_connection(server.server_address, timeout=2)
    connection.sendall(
        b"POST /mcp HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n{"
    )
    response = http.client.HTTPResponse(connection)
    response.begin()
    assert response.status == 400
    response.read()
    connection.close()
    assert not captured


def test_concurrency_limit_rejects_excess_connection(running):
    server = running[0]
    # Exhaust all worker slots without changing the production implementation.
    slots = server.config.concurrency
    for _ in range(slots):
        assert server._slots.acquire(blocking=False)
    try:
        connection = socket.create_connection(server.server_address, timeout=2)
        connection.settimeout(2)
        assert connection.recv(1) == b""
        connection.close()
    finally:
        for _ in range(slots):
            server._slots.release()


def test_rate_limit(running):
    server, request, _ = running
    server.config = dataclasses.replace(server.config, request_rate=1)
    assert request(method="GET", path=META)[0] == 200
    assert request(method="GET", path=META)[0] == 429


@pytest.mark.parametrize(
    "tool",
    [
        "get_status",
        "get_containers",
        "get_models",
        "get_storage",
        "get_network_health",
        "get_recent_alerts",
    ],
)
def test_proxy_gateway_snapshot_compatibility(running, config, keys, monkeypatch, tool):
    from workspace.runtime import mcp_readonly_adapter as adapter
    from workspace.runtime import mcp_readonly_gateway as gateway

    monkeypatch.setattr(gateway, "load_token", lambda _: INTERNAL)
    monkeypatch.setattr(gateway.mcp_cache_policy, "validate_cache", lambda _: None)
    monkeypatch.setattr(adapter, "_state", snapshot)
    backend = gateway.Gateway(("127.0.0.1", 0), "unused")
    thread = threading.Thread(
        target=backend.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()

    def forward(body, internal):
        connection = http.client.HTTPConnection(*backend.server_address, timeout=3)
        connection.request(
            "POST",
            "/mcp",
            body,
            {"Authorization": "Bearer " + internal, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        status, raw = response.status, response.read()
        connection.close()
        return status, raw

    running[0].upstream = forward
    try:
        token = encode(config, keys[0])
        for identity in ("fixture", 123):
            status, _, body = running[1](
                rpc(
                    identity=identity,
                    params={"name": tool, "arguments": {}, "_meta": {"fixture": True}},
                ),
                auth="Bearer " + token,
            )
            assert status == 200
            result = json.loads(body)
            assert result["id"] == identity and type(result["id"]) is type(identity)
            value = json.loads(result["result"]["content"][0]["text"])
            assert value["snapshot"]["generation"]
            assert value["snapshot"]["state"] in {"fresh", "stale"}
        initialization = rpc("initialize", params={"protocolVersion": "2025-11-25"})
        assert (
            json.loads(running[1](initialization)[2])["result"]["protocolVersion"] == "2025-11-25"
        )
        discovery = rpc("server/discover", params={})
        discovery["params"] = {}
        assert json.loads(running[1](discovery)[2])["result"]["supportedVersions"] == ["2025-11-25"]
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert running[1](notification)[0::2] == (202, b"")
    finally:
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=3)
