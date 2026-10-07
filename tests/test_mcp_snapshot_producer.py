import json
from datetime import UTC, datetime, timedelta

import pytest
from workspace.runtime.mcp_snapshot_contract import freshness, validate_snapshot
from workspace.runtime.mcp_snapshot_producer import Sources, build_snapshot

NOW = datetime(2026, 10, 6, tzinfo=UTC)


def snapshot(now=NOW):
    collectors = {
        "containers": lambda: {"containers": [{"name": "worker", "up": True, "status": "running"}]},
        "models": lambda: {
            "ollama_notebook": {
                "available": True,
                "models": [{"name": "llama:8b", "loaded": True, "size": 123}],
            },
            "ollama_nas": {},
            "model": {},
        },
        "storage": lambda: {"disk": {"free_gb": 12}},
        "network": lambda: {
            "internet": {"healthy": 1},
            "vpn": {},
            "network_devices": {"total": 4, "online": 3, "missing": 1},
        },
    }
    return build_snapshot(collectors, now=now)


def test_fresh_stale_and_future():
    data = snapshot()
    assert freshness(data, NOW)["state"] == "fresh"
    assert freshness(data, NOW + timedelta(seconds=301))["state"] == "stale"
    assert freshness(data, NOW - timedelta(seconds=1))["state"] == "stale"


def test_failed_sources_keep_old_values_without_refresh():
    old = snapshot()
    data = build_snapshot({}, old, now=NOW + timedelta(seconds=60))
    assert data["generation"] != old["generation"]
    assert data["containers"] == old["containers"]
    assert data["sources"]["containers"]["observed_at"] == old["generated_at"]
    assert freshness(data, NOW + timedelta(seconds=60), "containers")["state"] == "stale"


@pytest.mark.parametrize(
    "field",
    [
        "ip",
        "mac",
        "port",
        "devices",
        "topology",
        "credentials",
        "tokens",
        "headers",
        "environment",
        "config",
        "path",
    ],
)
def test_private_fields_rejected(field):
    data = snapshot()
    data["network_devices"][field] = "private"
    with pytest.raises(ValueError):
        validate_snapshot(data)


@pytest.mark.parametrize(
    "text",
    [
        "192.168.2.6",
        "fe80::1",
        "AA:BB:CC:DD:EE:FF",
        "host:8080",
        "/opt/private",
        "Bearer abc",
        "password=abc",
        "%31%39%32.168.2.6",
        "token",
        "x" * 40,
    ],
)
def test_private_strings_rejected(text):
    data = snapshot()
    data["containers"][0]["name"] = text
    with pytest.raises(ValueError):
        validate_snapshot(data)


@pytest.mark.parametrize("ttl", [0, -1, 3601, True, "300"])
def test_invalid_ttl(ttl):
    with pytest.raises(ValueError):
        build_snapshot({}, ttl=ttl)


def test_source_fixtures_and_stale_inventory():
    data = snapshot()
    payloads = {
        "http://fixture/inventory": json.dumps(
            {"observed_at": data["generated_at"], "containers": data["containers"]}
        ).encode(),
        "http://fixture/storage": json.dumps(
            {
                "observed_at": data["generated_at"],
                "total_gb": 20,
                "used_gb": 8,
                "free_gb": 12,
                "used_pct": 40,
            }
        ).encode(),
        "http://fixture/network": json.dumps(
            {
                "observed_at": data["generated_at"],
                "total": 4,
                "online": 3,
                "missing": 1,
                "healthy": None,
                "unhealthy": None,
                "latency_ms": None,
            }
        ).encode(),
        "http://fixture/ollama/api/tags": b'{"models":[{"name":"llama:8b","size":123,"digest":"private"}]}',
        "http://fixture/ollama/api/ps": b'{"models":[{"name":"llama:8b","details":{"private":"value"}}]}',
    }
    sources = Sources(
        fetch=payloads.__getitem__,
        inventory_url="http://fixture/inventory",
        storage_url="http://fixture/storage",
        network_url="http://fixture/network",
        ollama_url="http://fixture/ollama",
    )
    result = build_snapshot(
        {k: getattr(sources, k) for k in ("containers", "models", "storage", "network")},
        now=NOW + timedelta(seconds=600),
    )
    assert freshness(result, NOW + timedelta(seconds=600), "containers")["state"] == "stale"
    assert result["network_devices"]["total"] == 4
    assert result["network_devices"]["healthy"] is None
    assert result["sources"]["network"]["observed_at"] == data["generated_at"]
    assert "private" not in json.dumps(result)


def test_invalid_source_does_not_publish_private_values():
    old = snapshot()
    result = build_snapshot(
        {"network": lambda: {"internet": {}, "vpn": {}, "network_devices": {"ip": "192.168.2.6"}}},
        old,
        now=NOW,
    )
    assert result["network_devices"] == old["network_devices"]
    assert result["sources"]["network"]["available"] is False


def test_nas_inventory_is_sanitized():
    from types import SimpleNamespace

    from tools.mcp_nas_inventory import inventory

    def run(args, **kwargs):
        assert args[:3] == ["/usr/local/bin/docker", "ps", "-a"]
        return SimpleNamespace(
            stdout=json.dumps(
                {"Names": "worker", "State": "running", "Ports": "private", "Networks": "private"}
            )
        )

    assert inventory(run)["containers"] == [{"name": "worker", "up": True, "status": "running"}]


@pytest.mark.parametrize(
    "change",
    [
        {"generated_at": "2026-10-06T00:00:00"},
        {"generation": "invalid"},
        {"schema_version": True},
        {"alerts": [{"title": "private"}]},
        {"headers": {}},
        {"disk": {"free_gb": float("inf")}},
        {"network_devices": {"total": True}},
    ],
)
def test_strict_schema(change):
    data = snapshot()
    data.update(change)
    with pytest.raises(ValueError):
        validate_snapshot(data)


def test_http_rejects_sensitive_url_configuration():
    from workspace.runtime.mcp_snapshot_producer import ReadOnlyHTTP

    http = ReadOnlyHTTP()
    for url in (
        "file:///private",
        "http://user:secret@fixture/metrics",
        "http://fixture/metrics?token=private",
        "http://fixture/metrics#private",
    ):
        with pytest.raises(ValueError):
            http.get(url)


def producer_config():
    return {
        "inventory_url": "http://fixture/inventory",
        "storage_url": "http://fixture/storage",
        "network_url": "http://fixture/network",
        "ollama_url": "http://fixture/ollama",
        "ttl_seconds": 300,
        "timeout_seconds": 5,
    }


def test_explicit_config_and_network_allowlist():
    from workspace.runtime.mcp_snapshot_producer import configured_sources

    sources, ttl = configured_sources(producer_config())
    assert ttl == 300
    assert sources.fetch.__self__.allowed_urls == {
        "http://fixture/inventory",
        "http://fixture/storage",
        "http://fixture/network",
        "http://fixture/ollama/api/tags",
        "http://fixture/ollama/api/ps",
    }
    with pytest.raises(ValueError, match="allowlisted"):
        sources.fetch("http://other.fixture/metrics")


@pytest.mark.parametrize(
    "change",
    [
        {"ttl_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"storage_url": "file:///private"},
        {"network_url": "http://fixture/network?private=yes"},
        {"inventory_url": "http://user:private@fixture/inventory"},
        {"ollama_url": "http://fixture/ollama?private=yes"},
        {"environment": {}},
    ],
)
def test_invalid_producer_config(change):
    from workspace.runtime.mcp_snapshot_producer import configured_sources

    config = producer_config()
    config.update(change)
    with pytest.raises(ValueError):
        configured_sources(config)


@pytest.mark.parametrize(
    "stamp", ["2026-10-06Z", "2026-10-06 00:00:00Z", "2026-10-06T00:00:00+01:00Z"]
)
def test_explicit_utc_format(stamp):
    data = snapshot()
    data["generated_at"] = stamp
    with pytest.raises(ValueError):
        validate_snapshot(data)


def test_future_observation_rejected_and_global_staleness():
    data = snapshot()
    data["generated_at"] = (NOW + timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
    assert freshness(data, NOW + timedelta(seconds=600))["state"] == "stale"
    data["sources"]["containers"]["observed_at"] = (
        (NOW + timedelta(seconds=601)).isoformat().replace("+00:00", "Z")
    )
    with pytest.raises(ValueError):
        validate_snapshot(data)


def test_naive_clock_rejected():
    with pytest.raises(ValueError):
        build_snapshot({}, now=datetime(2026, 10, 6))


def test_cli_failure_logs_no_config_or_exception(tmp_path, caplog):
    from workspace.runtime.mcp_snapshot_producer import main

    config = tmp_path / "producer.json"
    config.write_text('{"credential":"private-fixture-value"}')
    assert main(["--cache-root", str(tmp_path), "--config-file", str(config)]) == 1
    assert "private-fixture" not in caplog.text


def test_generation_timestamp_is_after_collection(monkeypatch):
    from workspace.runtime import mcp_snapshot_producer as producer

    ticks = iter(NOW + timedelta(seconds=i) for i in range(50))

    class Clock:
        @staticmethod
        def now(_):
            return next(ticks)

    monkeypatch.setattr(producer, "datetime", Clock)
    collectors = {
        "containers": lambda: {"containers": []},
        "models": lambda: {"ollama_notebook": {}, "ollama_nas": {}, "model": {}},
        "storage": lambda: {"disk": {}},
        "network": lambda: {"internet": {}, "vpn": {}, "network_devices": {}},
    }
    result = producer.build_snapshot(collectors)
    assert all(
        source["observed_at"] < result["generated_at"] for source in result["sources"].values()
    )
