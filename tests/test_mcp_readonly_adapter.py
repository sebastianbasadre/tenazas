import json
import ast
import os
from pathlib import Path

import pytest

from workspace.runtime import mcp_readonly_adapter as mcp


def test_tool_allowlist_is_exact():
    assert set(mcp.TOOLS) == {
        "get_status", "get_containers", "get_models", "get_storage",
        "get_network_health", "get_recent_alerts",
    }
    assert set(mcp._HANDLERS) == set(mcp.TOOLS)
    assert len(mcp.TOOLS) == 6


@pytest.mark.parametrize("name", [
    "restart_container", "exec", "shell", "write_file", "delete_file",
    "docker", "ssh", "fetch_url", "restart", "write", "delete", "Docker", "SSH",
    "get_status; shell", "get_status/../../exec", "GET_STATUS", "get_status ",
    None, [], {}, 1,
])
def test_mutating_or_generic_tools_fail_closed(name):
    with pytest.raises(ValueError, match="not allowed"):
        mcp.call_tool(name)


def test_sanitize_removes_secrets_recursively():
    value = {
        "ok": 1,
        "token": "x",
        "nested": {"password": "y", "safe": True},
        "items": [{"authorization": "z", "name": "a"}],
    }
    assert mcp._sanitize(value) == {
        "ok": 1,
        "nested": {"safe": True},
        "items": [{"name": "a"}],
    }


def test_containers_reads_cached_state_only(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "containers": [
            {"name": "openclaw", "up": True, "status": "Up 1 hour", "token": "bad"},
            {"name": "piper", "up": False, "status": "Exited (0)"},
        ]
    }))
    monkeypatch.setattr(mcp, "STATE_FILE", state)
    result = mcp.get_containers()
    assert result["total"] == 2
    assert result["up"] == 1
    assert result["down"] == 1
    assert "token" not in json.dumps(result).lower()


def test_source_has_no_execution_primitives():
    source = Path(mcp.__file__).read_text(encoding="utf-8")
    forbidden = ["subprocess", "os.system", "docker.sock", "paramiko", "pty.", "Popen("]
    for item in forbidden:
        assert item not in source


@pytest.fixture
def cache(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    infra = tmp_path / "infra.json"
    incidents = tmp_path / "incidents"
    incidents.mkdir()
    monkeypatch.setattr(mcp, "STATE_FILE", state)
    monkeypatch.setattr(mcp, "AGENT_STATUS_FILE", infra)
    monkeypatch.setattr(mcp, "INCIDENTS_DIR", incidents)
    return state, infra, incidents


@pytest.mark.parametrize("key", [
    "access_token", "refreshToken", "ID_TOKEN", "X-API-Key", "API.KEY",
    "api%5Fkey", "Set-Cookie", "cookies", "Authorization", "client_secret",
    "ssh_private_key", "AWS_ACCESS_KEY_ID", "password_hash", "credentials",
    "headers", "env", "config",
])
def test_credential_key_variants_are_removed(key):
    assert mcp._sanitize({key: "canary-value", "ok": True}) == {"ok": True}


@pytest.mark.parametrize("value", [
    "Authorization: Bearer canary-value", "password=canary-value",
    "Cookie: sid=canary-value", "API-Key: canary-value",
    "https://user:canary-value@example.invalid/", "https://x.invalid/?api_key=canary-value",
    "Bearer%20canary-value", '"password": "canary-value"',
    "-----BEGIN OPENSSH PRIVATE KEY-----\ncanary-value",
    "sk-test-canaryvalue", "ghp_canaryvalue", "123456789:abcdefghijklmnopqrstuvwxyz123456789",
    "abcdefghijklmnopqrstuvwxyz0123456789",
])
def test_free_text_credentials_are_redacted(value, cache):
    state, _, incidents = cache
    state.write_text(json.dumps({"containers": [{"name": value, "status": value}],
                                 "model": value, "vpn": {"status": value}, "ts": value}))
    (incidents / "one.json").write_text(json.dumps({"title": value, "incident_id": value}))
    for name in mcp.TOOLS:
        assert value not in json.dumps(mcp.call_tool(name))
    assert mcp._sanitize(value) == "[REDACTED]"


def test_unknown_cached_fields_are_not_forwarded(cache):
    state, infra, _ = cache
    state.write_text(json.dumps({"model": {"ok": True, "unexpected": "canary-value"},
                                "disk": {"free_gb": 42, "credentials_blob": "canary-value"},
                                "internet": {"ok": True, "logs": "canary-value"}}))
    infra.write_text(json.dumps({"sub": "disk", "raw": {"free_gb": 42, "unexpected": "canary-value"}}))
    assert "canary-value" not in json.dumps(mcp.get_status())
    assert mcp.get_storage()["disk"] == {"free_gb": 42}


@pytest.mark.parametrize("payload", ["[]", "null", '"text"', "{broken", '{"a": "\ud800"}'])
def test_malformed_cache_fails_closed(payload, cache):
    state, infra, incidents = cache
    for path in (state, infra, incidents / "one.json"):
        path.write_bytes(payload.encode("utf-8", errors="surrogatepass"))
    for name in mcp.TOOLS:
        assert isinstance(mcp.call_tool(name), dict)


def test_read_paths_cannot_be_supplied_by_callers(cache, tmp_path):
    secret = tmp_path / "private.json"
    secret.write_text('{"password":"canary-value"}')
    assert mcp._load_json(secret, {}) == {}
    with pytest.raises(TypeError):
        mcp.call_tool("get_status", secret)


def test_hardlink_snapshots_are_rejected(cache, tmp_path):
    state, _, _ = cache
    private = tmp_path / "private.json"
    private.write_text('{"model":"canary-value"}')
    os.link(private, state)
    assert mcp.get_models()["model"] == {}


def test_symlink_snapshots_are_rejected(cache, tmp_path):
    state, _, incidents = cache
    private = tmp_path / "private.json"
    private.write_text('{"model":"canary-value"}')
    try:
        state.symlink_to(private)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    assert mcp.get_models()["model"] == {}
    (incidents / "one.json").symlink_to(private)
    assert mcp.get_recent_alerts() == {"alerts": []}


def test_every_operation_reads_only_configured_cache_without_external_effects(cache, monkeypatch):
    import socket
    import subprocess
    state, infra, incidents = cache
    incident = incidents / "one.json"
    for path in (state, infra, incident):
        path.write_text("{}")
    opened = []
    original = Path.open

    def read_only(path, mode="r", *args, **kwargs):
        assert path in {state, infra, incident}
        assert mode == "rb"
        opened.append(path)
        return original(path, mode, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("External effect is forbidden")

    monkeypatch.setattr(Path, "open", read_only)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for operation in mcp.TOOLS:
        assert isinstance(mcp.call_tool(operation), dict)
    if os.name != "posix":
        assert set(opened) == {state, infra, incident}


def test_unknown_operation_does_not_read_anything(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unknown operation reached file I/O")
    monkeypatch.setattr(mcp, "_load_json", forbidden)
    with pytest.raises(ValueError):
        mcp.call_tool("exec")


def test_inputs_and_output_are_bounded(cache):
    state, _, _ = cache
    state.write_bytes(b" " * (mcp._MAX_BYTES + 1))
    assert mcp.get_containers()["total"] == 0
    state.write_text(json.dumps({"containers": [{"name": "service", "up": True}] * 200}))
    assert mcp.get_containers()["total"] == 100
    assert mcp._sanitize(float("nan")) is None
    assert mcp._sanitize(10**400) is None


def test_ast_has_only_read_only_standard_library_capabilities():
    tree = ast.parse(Path(mcp.__file__).read_text(encoding="utf-8"))
    allowed = {"__future__", "json", "math", "os", "re", "stat", "itertools", "pathlib", "typing", "urllib.parse"}
    forbidden_calls = {"exec", "eval", "compile", "__import__", "system", "popen", "Popen",
                       "run", "write", "write_text", "write_bytes", "unlink", "remove", "mkdir",
                       "rmdir", "rename", "replace", "touch", "chmod", "chown", "connect", "send"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            regex_compile = (name == "compile" and isinstance(node.func, ast.Attribute)
                             and isinstance(node.func.value, ast.Name) and node.func.value.id == "re")
            assert regex_compile or name not in forbidden_calls
            if name == "open":
                assert isinstance(node.func, ast.Attribute)
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    flags = ast.unparse(node.args[1])
                    assert "O_RDONLY" in flags and "O_NOFOLLOW" in flags
                    assert not any(flag in flags for flag in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"))
                else:
                    assert isinstance(node.args[0], ast.Constant) and node.args[0].value == "rb"
            if name == "fdopen":
                assert node.args[1].value == "rb"
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                assert name in {"fstat", "open", "close", "fdopen"}
