import json
from datetime import timedelta

import pytest
from tests.test_mcp_snapshot_producer import NOW, snapshot
from workspace.runtime import mcp_readonly_adapter as adapter


@pytest.mark.parametrize("name", adapter.TOOLS)
def test_six_tools(name, monkeypatch):
    monkeypatch.setattr(adapter, "_state", snapshot)
    result = adapter.call_tool(name)
    assert result["snapshot"]["generation"]
    assert "192.168" not in json.dumps(result)
    if name == "get_recent_alerts":
        assert result["alerts"] == []


def test_status_single_read_and_generation(monkeypatch):
    calls = []

    def read():
        calls.append(1)
        return snapshot()

    monkeypatch.setattr(adapter, "_state", read)
    result = adapter.call_tool("get_status")
    assert len(calls) == 1
    generation = result["snapshot"]["generation"]
    assert all(
        result[key]["snapshot"]["generation"] == generation
        for key in ("containers", "models", "storage", "network", "alerts")
    )


def test_stale_returns_last_data():
    data = snapshot()
    result = adapter._result("get_containers", data, NOW + timedelta(seconds=301))
    assert result["containers"] == data["containers"]
    assert result["snapshot"]["state"] == "stale"


def test_unknown_tool_forbidden():
    with pytest.raises(ValueError):
        adapter.call_tool("restart_container")


def test_bad_snapshot_rejected(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"containers": [], "token": "private"}')
    monkeypatch.setattr(adapter, "STATE_FILE", path)
    with pytest.raises(ValueError):
        adapter._state()
