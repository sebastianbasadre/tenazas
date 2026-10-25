"""Sanitized source tests: fake collectors, temporary files and loopback only."""

import http.client
import json
import logging
import threading
from types import SimpleNamespace

import pytest
from workspace.runtime import mcp_sanitized_sources as source
from workspace.runtime.mcp_snapshot_producer import Sources, build_snapshot
from workspace.runtime.mcp_source_contract import network_payload, storage_payload, validate_feed

STAMP = "2026-10-06T00:00:00Z"


class Fixtures:
    def inventory(self):
        return {
            "observed_at": STAMP,
            "containers": [{"name": "worker", "up": True, "status": "running"}],
        }

    def storage(self):
        return storage_payload(2_000_000_000, 500_000_000, STAMP)

    def network(self):
        return network_payload(
            [
                {
                    "ip": "private",
                    "mac": "private",
                    "name": "private",
                    "labels": {"private": "private"},
                }
            ],
            STAMP,
        )


@pytest.fixture
def server():
    store = source.SourceStore()
    store.refresh(Fixtures())
    instance = source.SourceServer(("127.0.0.1", 0), store)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()

    def request(path, method="GET", headers=None):
        conn = http.client.HTTPConnection(*instance.server_address, timeout=2)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    yield instance, request
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=2)


def test_media_route_serves_validated_memory_and_isolates_failed_file(server, tmp_path):
    from tests.test_mcp_media_extension import state

    instance, request = server
    assert request("/media")[0] == 404
    path = tmp_path / "media.json"
    media = state()
    path.write_text(json.dumps(media))
    instance.store.media_file = path
    instance.store.refresh(Fixtures())
    assert json.loads(request("/media")[1])["generation"] == media["generation"]
    path.unlink()
    # HTTP handlers perform neither file reads nor live collection.
    assert request("/media")[0] == 200
    instance.store.refresh(Fixtures())
    assert request("/media")[0] == 503
    assert request("/inventory")[0] == 200
    assert request("/media", "POST")[0] == 405


def test_inventory_drops_sensitive_docker_fields():
    from tools.mcp_nas_inventory import inventory

    def run(args, **kwargs):
        assert args[:3] == ["/usr/local/bin/docker", "ps", "-a"]
        assert "{{json .}}" not in args
        raw = {
            "Names": "worker",
            "State": "running",
            "Env": ["private"],
            "Labels": {"private": "private"},
            "Mounts": ["private"],
            "Command": "private",
            "Ports": "private",
        }
        return SimpleNamespace(stdout=json.dumps(raw))

    result = inventory(run)
    validate_feed("/inventory", result)
    assert set(result["containers"][0]) == {"name", "up", "status"}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "extra", ["env", "mounts", "commands", "labels", "ports", "token", "credentials"]
)
def test_extra_inventory_fields_rejected(extra):
    data = Fixtures().inventory()
    data["containers"][0][extra] = "private"
    with pytest.raises(ValueError):
        validate_feed("/inventory", data)


def test_network_only_aggregates_and_optional_nulls():
    payload = network_payload(
        [
            {"ip": "private-ip", "mac": "private-mac", "name": "private-name"},
            {"missing_since": STAMP, "token": "private-token"},
        ],
        STAMP,
    )
    assert payload == {
        "observed_at": STAMP,
        "total": 2,
        "online": 1,
        "missing": 1,
        "healthy": None,
        "unhealthy": None,
        "latency_ms": None,
    }
    assert "private" not in json.dumps(payload)


def test_decimal_gb_conversion_and_zero():
    data = storage_payload(2_000_000_000, 500_000_000, STAMP)
    assert (data["total_gb"], data["used_gb"], data["free_gb"], data["used_pct"]) == (
        2,
        0.5,
        1.5,
        25,
    )
    assert storage_payload(1024**3, 0, STAMP)["total_gb"] == 1.073741824
    assert storage_payload(0, 0, STAMP)["used_pct"] == 0


@pytest.mark.parametrize("total,used", [(0, 1), (1, 2), (-1, 0), (True, 0), (float("nan"), 0)])
def test_invalid_storage_bytes(total, used):
    with pytest.raises(ValueError):
        storage_payload(total, used, STAMP)


def test_storage_requires_confirmed_mount(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        source.NASCollectors(
            filesystem="/",
            filesystem_confirmed=True,
        )
    with pytest.raises(ValueError):
        source.NASCollectors(
            filesystem=tmp_path,
            filesystem_confirmed=False,
        )
    collector = source.NASCollectors(
        filesystem=tmp_path,
        filesystem_confirmed=True,
        inventory_collector=Fixtures().inventory,
    )
    monkeypatch.setattr(source.os.path, "ismount", lambda _: False)
    with pytest.raises(ValueError):
        collector.storage()
    monkeypatch.setattr(source.os.path, "ismount", lambda _: True)
    monkeypatch.setattr(
        source.os,
        "statvfs",
        lambda p: SimpleNamespace(f_frsize=1000, f_blocks=2_000_000, f_bavail=1_500_000),
        raising=False,
    )
    assert collector.storage()["total_gb"] == 2


def test_network_requires_separate_signal_without_metrics_fallback(tmp_path):
    collector = source.NASCollectors(
        filesystem=tmp_path, filesystem_confirmed=True,
        inventory_collector=Fixtures().inventory,
    )
    with pytest.raises(ValueError, match="Discovery result path required"):
        collector.network()


@pytest.mark.parametrize(
    "field,value",
    [
        ("ip", "private"),
        ("healthy", True),
        ("online", None),
        ("latency_ms", "private"),
        ("missing", -1),
    ],
)
def test_network_private_or_invalid_values_rejected(field, value):
    data = Fixtures().network()
    data[field] = value
    with pytest.raises(ValueError):
        validate_feed("/network", data)


def test_naive_observation_rejected():
    with pytest.raises(ValueError):
        network_payload([], "2026-10-06T00:00:00")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def test_get_only(server, method):
    status, body = server[1]("/inventory", method)
    assert status == 405
    if method == "HEAD":
        assert body == b""


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/exec",
        "/inventory?path=/private",
        "/network?url=http://private",
        "/../private",
        "/metrics",
    ],
)
def test_no_arbitrary_routes_or_parameters(server, path):
    assert server[1](path)[0] == 404


@pytest.mark.parametrize("bind", ["0.0.0.0", "8.8.8.8", "::", "localhost"])
def test_public_or_ambiguous_bind_rejected(bind):
    with pytest.raises(ValueError):
        source.SourceServer((bind, 0), source.SourceStore())


def test_failure_is_503_and_safe_logging(server, caplog):
    class Broken:
        def __getattr__(self, _):
            raise RuntimeError("private-token-value")

    server[0].store.refresh(Broken())
    with caplog.at_level(logging.INFO):
        status, body = server[1]("/inventory")
        server[1]("/network?token=private-token-value")
    assert status == 503 and b"private" not in body
    assert "private-token" not in caplog.text


def test_http_does_not_invoke_collectors_or_network(server, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Unexpected collector/network access")

    monkeypatch.setattr(source.NASCollectors, "inventory", forbidden)
    monkeypatch.setattr(source.NASCollectors, "network", forbidden)
    assert server[1]("/inventory")[0] == 200
    assert server[1]("/storage")[0] == 200


def test_producer_consumes_json_feeds_preserves_nulls_and_dates():
    fixtures = Fixtures()
    data = {
        "inventory": fixtures.inventory(),
        "storage": fixtures.storage(),
        "network": fixtures.network(),
    }
    payloads = {f"http://fixture/{key}": json.dumps(value).encode() for key, value in data.items()}
    payloads.update(
        {
            "http://fixture/ollama/api/tags": b'{"models":[]}',
            "http://fixture/ollama/api/ps": b'{"models":[]}',
        }
    )
    collectors = Sources(
        fetch=payloads.__getitem__,
        inventory_url="http://fixture/inventory",
        storage_url="http://fixture/storage",
        network_url="http://fixture/network",
        ollama_url="http://fixture/ollama",
    )
    state = build_snapshot(
        {key: getattr(collectors, key) for key in ("containers", "storage", "network", "models")}
    )
    assert state["disk"]["total_gb"] == 2
    assert state["network_devices"]["latency_ms"] is None
    assert state["sources"]["network"]["observed_at"] == STAMP
    assert state["sources"]["storage"]["observed_at"] == STAMP


def test_producer_fetches_real_loopback_json_routes(server):
    from workspace.runtime.mcp_snapshot_producer import ReadOnlyHTTP

    address = server[0].server_address
    base = f"http://{address[0]}:{address[1]}"
    http = ReadOnlyHTTP(allowed_urls=[base + route for route in source.ROUTES])
    collectors = Sources(
        fetch=http.get,
        inventory_url=base + "/inventory",
        storage_url=base + "/storage",
        network_url=base + "/network",
        ollama_url="http://unused.fixture",
    )
    state = build_snapshot(
        {key: getattr(collectors, key) for key in ("containers", "storage", "network")}
    )
    assert state["containers"][0]["name"] == "worker"
    assert state["disk"]["used_pct"] == 25
    assert state["network_devices"]["unhealthy"] is None
    assert state["sources"]["network"]["observed_at"] == STAMP
