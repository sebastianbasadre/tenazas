"""Local HTTP contract tests; credential/cache filesystem gates tested separately on Linux."""

import http.client
import json
import logging
import threading

import pytest
from tests.test_mcp_snapshot_producer import snapshot
from workspace.runtime import mcp_readonly_adapter as adapter
from workspace.runtime import mcp_readonly_gateway as gateway

TOKEN = "T" * 48  # Synthetic fixture, never a staging credential.


@pytest.fixture
def rpc(monkeypatch):
    monkeypatch.setattr(gateway, "load_token", lambda _: TOKEN)
    monkeypatch.setattr(gateway.mcp_cache_policy, "validate_cache", lambda _: None)
    monkeypatch.setattr(adapter, "_state", snapshot)
    server = gateway.Gateway(("127.0.0.1", 0), "unused", limit=1000)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(
        payload=None, *, path="/mcp", method="POST", authorization="Bearer " + TOKEN, headers=None
    ):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        body = json.dumps(payload) if payload is not None else None
        outgoing = {"Content-Type": "application/json"}
        if authorization is not None:
            outgoing["Authorization"] = authorization
        outgoing.update(headers or {})
        connection.request(method, path, body, outgoing)
        response = connection.getresponse()
        status, raw = response.status, response.read()
        connection.close()
        return status, json.loads(raw) if raw else None

    request.server = server
    yield request
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_application_tools_are_optional_typed_and_independent_of_infra_cache(rpc, monkeypatch):
    from workspace.runtime import mcp_applications_contract as contracts

    captured = []

    class Applications:
        def call(self, name, arguments):
            captured.append((name, arguments))
            return {"ok": True, "data": {"total": 0}}

    rpc.server.applications = Applications()
    tools = rpc(call("tools/list"))[1]["result"]["tools"]
    assert len(tools) == 17
    assert {t["name"] for t in tools} == set(adapter.TOOLS) | set(contracts.DEFINITIONS)
    response = rpc(call("tools/call", params={"name": "get_job_applications", "arguments": {}}))[1]
    assert json.loads(response["result"]["content"][0]["text"]) == {"total": 0}
    assert captured == [("get_job_applications", {})]
    response = rpc(
        call(
            "tools/call",
            params={"name": "get_job_applications", "arguments": {"owner": "another-user"}},
        )
    )[1]
    assert response["error"]["code"] == -32602 and len(captured) == 1


def call(method, identity=1, params=None):
    return {"jsonrpc": "2.0", "id": identity, "method": method, "params": params or {}}


@pytest.mark.parametrize("identity", [1, 0, -2, "request-1", ""])
def test_ids_preserved(rpc, identity):
    status, result = rpc(call("ping", identity))
    assert status == 200 and result["id"] == identity
    assert type(result["id"]) is type(identity)


@pytest.mark.parametrize("identity", [True, False, None, 1.2, [], {}, "x" * 65])
def test_invalid_ids(rpc, identity):
    assert rpc(call("ping", identity))[0] == 400


@pytest.mark.parametrize("method", [[], 123])
def test_invalid_method(rpc, method):
    assert rpc(call(method))[0] == 400


def test_initialize_discovery_and_six_tools(rpc):
    assert (
        rpc(call("initialize", params={"protocolVersion": "2025-11-25"}))[1]["result"][
            "protocolVersion"
        ]
        == "2025-11-25"
    )
    assert (
        rpc(call("initialize", params={"protocolVersion": "2026-07-28"}))[1]["error"]["code"]
        == -32602
    )
    discovery = rpc(call("server/discover"))[1]["result"]
    assert discovery["supportedVersions"] == ["2025-11-25"]
    tools = rpc(call("tools/list"))[1]["result"]["tools"]
    assert [t["name"] for t in tools] == list(adapter.TOOLS)
    assert len(tools) == 8
    assert all(
        t["annotations"]["readOnlyHint"]
        and not t["annotations"]["destructiveHint"]
        and t["securitySchemes"][0]["scopes"] == ["read:tenazas"]
        for t in tools
    )


@pytest.mark.parametrize("name", adapter.TOOLS[:6])
def test_tools_call_with_meta(rpc, name):
    status, response = rpc(
        call(
            "tools/call",
            "fixture",
            {"name": name, "arguments": {}, "_meta": {"request-id": "fixture"}},
        )
    )
    assert status == 200
    result = response["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["snapshot"]["generation"]


@pytest.mark.parametrize("name", ["get_media_library", "get_downloads", "get_status"])
def test_media_tools_and_existing_status_read_only(rpc, name, monkeypatch):
    from tests.test_mcp_media_extension import state
    from workspace.runtime import mcp_media_reader

    media = state()
    monkeypatch.setattr(mcp_media_reader, "read_state", lambda *args: media)
    monkeypatch.setattr(gateway.mcp_cache_policy, "expected_identity", lambda: None)
    status, response = rpc(call("tools/call", params={"name": name, "arguments": {}}))
    assert status == 200 and response["result"]["isError"] is False
    value = json.loads(response["result"]["content"][0]["text"])
    if name == "get_status":
        assert value["media_library"]["data"]["totals"]["movies"] == 12
        assert value["downloads"]["sources"]["sabnzbd"]["available"] is True
    else:
        assert value["snapshot"]["generation"] == media["generation"]


@pytest.mark.parametrize(
    "params",
    [
        {"name": "restart"},
        {"name": "get_status", "arguments": {"command": "private"}},
        {"name": "get_status", "_meta": "invalid"},
        {"name": "get_status", "headers": {}},
    ],
)
def test_forbidden_calls(rpc, params):
    assert rpc(call("tools/call", params=params))[1]["error"]["code"] == -32602


def test_safe_logs(rpc, caplog):
    with caplog.at_level(logging.INFO):
        rpc(
            call(
                "tools/call",
                params={
                    "name": "client-private-name",
                    "client-private-key": "client-private-value",
                },
            )
        )
        rpc(
            call(
                "tools/call",
                params={"name": "get_status", "_meta": {"Authorization": "client-private-value"}},
            )
        )
        rpc(path="/unrecognized?token=client-private-value", method="GET")
        rpc(authorization="Bearer client-private-value")
    assert "client-private" not in caplog.text
    assert TOKEN not in caplog.text
    assert "Authorization" not in caplog.text
    assert "tool=get_status" in caplog.text


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic private"])
def test_unauthorized(rpc, authorization):
    # Authenticate before parsing/reading a body. Header-only requests avoid the
    # Windows TCP reset race when the server closes with unread request bytes.
    assert rpc(authorization=authorization)[0] == 401


def test_notification_and_forbidden_methods(rpc):
    assert rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
    assert rpc(call("resources/write"))[1]["error"]["code"] == -32601
    assert rpc(headers={"Origin": "https://fixture.invalid"})[0] == 403


def test_policy_failure_returns_generic_error(rpc, monkeypatch):
    def fail(_):
        raise ValueError("private-path-token")

    monkeypatch.setattr(gateway.mcp_cache_policy, "validate_cache", fail)
    response = rpc(call("tools/call", params={"name": "get_status"}))[1]
    assert response["error"] == {"code": -32603, "message": "Read unavailable"}
