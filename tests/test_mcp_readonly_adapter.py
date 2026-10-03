import json
from pathlib import Path

import pytest

from workspace.runtime import mcp_readonly_adapter as mcp


def test_tool_allowlist_is_exact():
    assert set(mcp.TOOLS) == {
        "get_status", "get_containers", "get_models", "get_storage",
        "get_network_health", "get_recent_alerts",
    }


@pytest.mark.parametrize("name", [
    "restart_container", "exec", "shell", "write_file", "delete_file",
    "docker", "ssh", "fetch_url",
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
