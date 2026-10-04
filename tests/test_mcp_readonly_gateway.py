import io
import json
import logging
from email.message import Message
from types import SimpleNamespace

import pytest

from workspace.runtime import mcp_readonly_gateway as gateway

TOKEN = "a" * 48


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    path = tmp_path / "credential"
    path.write_text(TOKEN)
    path.chmod(0o600)
    monkeypatch.setattr(gateway.adapter, "call_tool", lambda name: {"sanitized": name})

    def call(request=None, *, authorization="Bearer " + TOKEN, headers=None, method="POST", target="/mcp", raw=None):
        handler = object.__new__(gateway.Handler)
        handler.server = SimpleNamespace(token_file=path, permitted=lambda: True)
        handler.path = target
        handler.headers = Message()
        if authorization is not None:
            handler.headers["Authorization"] = authorization
        body = raw if raw is not None else json.dumps(request or {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.headers["Content-Type"] = "application/json"
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO(body)
        result = []
        handler.reply = lambda status, payload=None: result.append((status, payload))
        getattr(handler, "do_" + method)()
        return result[0]
    return call, path


@pytest.mark.parametrize("authorization", [None, "", "Bearer wrong", "Basic " + TOKEN, "bearer " + TOKEN, "Bearer " + TOKEN + "x"])
def test_invalid_auth_never_reads_cache(invoke, monkeypatch, authorization):
    monkeypatch.setattr(gateway.adapter, "call_tool", lambda *_: pytest.fail("Unauthorized cache read"))
    call, _ = invoke
    status, payload = call(authorization=authorization)
    assert status == 401 and payload == {"error": "unauthorized"}


def test_duplicate_authorization_rejected(invoke):
    call, _ = invoke
    assert call(headers={"Authorization": "Bearer " + TOKEN})[0] == 401


def test_tools_are_exact(invoke):
    call, _ = invoke
    status, payload = call()
    assert status == 200
    assert [tool["name"] for tool in payload["result"]["tools"]] == list(gateway.adapter.TOOLS)


@pytest.mark.parametrize("name", gateway.adapter.TOOLS)
def test_valid_tools(invoke, name):
    call, _ = invoke
    status, result = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": {}}})
    assert status == 200 and result["result"]["isError"] is False


@pytest.mark.parametrize("name", ["restart", "exec", "shell", "write", "delete", "Docker", "SSH", [], {}, None])
def test_forbidden_tools(invoke, name):
    call, _ = invoke
    assert call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name}})[1]["error"]["code"] == -32602


def test_arguments_cannot_pass_paths_or_commands(invoke):
    call, _ = invoke
    assert call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_status", "arguments": {"path": "/etc/passwd"}}})[1]["error"]


def test_rotation_revocation_and_missing_key(invoke):
    call, path = invoke
    path.write_text("b" * 48)
    assert call()[0] == 401
    assert call(authorization="Bearer " + "b" * 48)[0] == 200
    path.unlink()
    assert call()[0] == 503


@pytest.mark.parametrize("headers,status", [({"Origin": "https://evil.example"}, 403), ({"Transfer-Encoding": "chunked"}, 400), ({"Content-Length": "99999"}, 400)])
def test_origin_and_ambiguous_framing(invoke, headers, status):
    call, _ = invoke
    assert call(headers=headers)[0] == status


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_other_http_methods(invoke, method):
    call, _ = invoke
    assert call(method=method)[0] == 405


def test_secret_string_id_is_not_reflected(invoke):
    call, _ = invoke
    status, body = call({"jsonrpc": "2.0", "id": TOKEN, "method": "ping"})
    assert status == 400 and TOKEN not in json.dumps(body)


def test_private_credential_permissions(invoke):
    _, path = invoke
    path.chmod(0o644)
    with pytest.raises(ValueError):
        gateway.load_token(path)


def test_audit_never_logs_headers_or_request(caplog):
    handler = object.__new__(gateway.Handler)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda *_: None
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    with caplog.at_level(logging.INFO, logger="tenazas.mcp.access"):
        handler.log_message("%s", TOKEN)
        handler.reply(401, {"error": "unauthorized"})
    assert "status=401" in caplog.text and TOKEN not in caplog.text


@pytest.mark.parametrize("raw", [b'{', b'[]', b'null', b'\xff', b'{"jsonrpc":"1.0"}'])
def test_malformed_body_fails_closed(invoke, raw):
    call, _ = invoke
    assert call(raw=raw)[0] == 400


def test_backend_error_never_reflects_secrets(invoke, monkeypatch):
    call, _ = invoke
    def fail(_name):
        raise RuntimeError(TOKEN)
    monkeypatch.setattr(gateway.adapter, "call_tool", fail)
    status, payload = call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_status"}})
    assert status == 200 and payload["error"]["code"] == -32603
    assert TOKEN not in json.dumps(payload)


def test_initialize_and_notification(invoke):
    call, _ = invoke
    assert call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": gateway.PROTOCOL}})[1]["result"]["protocolVersion"] == gateway.PROTOCOL
    assert call({"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)


def test_rate_counter_is_bounded_without_server_start():
    import threading
    import time
    server = object.__new__(gateway.Gateway)
    server._lock = threading.Lock()
    server._requests, server._start = 0, time.monotonic()
    server.limit, server.window = 1, 60
    assert server.permitted() is True
    assert server.permitted() is False
    server._start -= 61
    assert server.permitted() is True
