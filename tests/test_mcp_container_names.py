import json
from types import SimpleNamespace

import pytest
from tests.test_mcp_snapshot_producer import snapshot
from tools.mcp_nas_inventory import inventory
from workspace.runtime.mcp_sanitized_sources import SourceStore
from workspace.runtime.mcp_snapshot_contract import container_name, public_text, validate_snapshot
from workspace.runtime.mcp_source_contract import validate_feed

GOOD = [
    "tenazas-24x7-observation",
    "tenazas-recovery-watchdog",
    "nginx",
    "redis-1",
    "postgres_db",
    "app.worker-2",
    "openclaw-network-exporter-1",
]
BAD = [
    "",
    None,
    123,
    "x" * 24,
    "a-" + "Z9" * 12,
    "a" * 121,
    "Bearer-value",
    "api-key-value",
    "password-db",
    "secret-db",
    "access-token",
    "credentials-db",
    "jwt-value",
    "session-id",
    "ssh-agent",
    "sk-or-v1-private",
    "ghp_private",
    "github_pat_private",
    "AKIA" + "A" * 16,
    "192.168.2.2",
    "db-192.168.2.2",
    "fe80::1",
    "AA:BB:CC:DD:EE:FF",
    "aa-bb-cc-dd-ee-ff",
    "aabb.ccdd.eeff",
    "550e8400-e29b-41d4-a716-446655440000",
    "/volume1/private",
    "..",
    "../private",
    "C:\\private",
    "https://private",
    "user@host",
    "db:8080",
    "unsafe name",
    "unsafe;exec",
    "unsafe\nname",
    "%31%39%32.168.2.2",
    ".hidden",
    "-worker",
    "worker--db",
    "worker__db",
]


@pytest.mark.parametrize("name", GOOD)
def test_safe_container_names_across_collector_feed_and_snapshot(name):
    assert container_name(name) == name

    def run(args, **kwargs):
        assert args[:3] == ["/usr/local/bin/docker", "ps", "-a"]
        return SimpleNamespace(
            stdout=json.dumps({"Names": name, "State": "running", "Env": ["private"]})
        )

    data = inventory(run)
    validate_feed("/inventory", data)
    assert data["containers"] == [{"name": name, "up": True, "status": "running"}]
    state = snapshot()
    state["containers"] = data["containers"]
    validate_snapshot(state)


@pytest.mark.parametrize("name", BAD)
def test_unsafe_container_names_rejected_at_every_boundary(name):
    with pytest.raises(ValueError):
        container_name(name)
    feed = {
        "observed_at": "2026-10-06T00:00:00Z",
        "containers": [{"name": name, "up": True, "status": "running"}],
    }
    with pytest.raises(ValueError):
        validate_feed("/inventory", feed)
    state = snapshot()
    state["containers"] = feed["containers"]
    with pytest.raises(ValueError):
        validate_snapshot(state)


@pytest.mark.parametrize("name", GOOD[:2])
def test_generic_public_text_is_not_relaxed(name):
    with pytest.raises(ValueError):
        public_text(name)
    state = snapshot()
    state["ollama_notebook"]["models"][0]["name"] = name
    with pytest.raises(ValueError):
        validate_snapshot(state)


def test_source_store_no_long_name_503():
    class Collectors:
        def inventory(self):
            return {
                "observed_at": "2026-10-06T00:00:00Z",
                "containers": [{"name": n, "up": True, "status": "running"} for n in GOOD[:2]],
            }

    store = SourceStore()
    store.refresh(Collectors())
    assert [row["name"] for row in json.loads(store.get("/inventory"))["containers"]] == GOOD[:2]
