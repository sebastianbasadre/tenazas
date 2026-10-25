"""Local mocks only: no scanner, remote machine or credential access."""

import ast
import copy
import http.client
import json
import os
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from workspace.runtime import device_discovery as discovery
from workspace.runtime import mcp_sanitized_sources as source
from workspace.runtime import network_discovery_result as result
from workspace.runtime.mcp_snapshot_contract import freshness
from workspace.runtime.mcp_snapshot_producer import ReadOnlyHTTP, Sources, build_snapshot

START = datetime(2026, 10, 8, 14, tzinfo=UTC)


def output(hosts):
    reports = "".join(f"Nmap scan report for {ip}\nHost is up (0.001s latency).\n" for ip in hosts)
    return (
        reports + f"Nmap done: 256 IP addresses ({len(hosts)} hosts up) scanned in 1.00 seconds\n"
    )


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "INVENTORY_FILE", tmp_path / "inventory.json")
    monkeypatch.setattr(discovery, "RESULT_FILE", tmp_path / "result.json")
    monkeypatch.setattr(discovery, "_log", lambda _: None)
    monkeypatch.setattr(discovery, "_enrich_with_arp", lambda devices: devices)
    monkeypatch.setattr(discovery, "classify_device", lambda device: device)
    clock = [START]
    monkeypatch.setattr(discovery, "utc_now", lambda: clock[0].isoformat().replace("+00:00", "Z"))
    monkeypatch.setattr(discovery, "now_iso", lambda: clock[0].isoformat())

    def forbidden(*args, **kwargs):
        raise AssertionError("Real subprocess forbidden")

    monkeypatch.setattr(discovery.subprocess, "run", forbidden)
    return tmp_path, clock


def scanner(monkeypatch, *, hosts=None, failure=None):
    def run(args, **kwargs):
        assert args[0] == "nmap" and kwargs["timeout"] == 30
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30, output="private-partial-output")
        if failure == "missing":
            raise FileNotFoundError("private-path")
        if failure == "execution_error":
            return SimpleNamespace(
                returncode=1, stdout=output(["192.168.2.20"]), stderr="private-error"
            )
        if failure == "parsing_error":
            return SimpleNamespace(returncode=0, stdout="truncated scan", stderr="")
        return SimpleNamespace(returncode=0, stdout=output(hosts or []), stderr="")

    monkeypatch.setattr(discovery.subprocess, "run", run)


def collector():
    return source.NASCollectors(
        filesystem=discovery.RESULT_FILE.parent,
        filesystem_confirmed=True,
        inventory_collector=lambda: {},
        network_result_file=discovery.RESULT_FILE,
    )


def snapshot_from_feed(feed, previous=None, now=START):
    def fetch(_):
        if feed is None:
            raise OSError("503 source unavailable")
        return json.dumps(feed).encode()

    sources = Sources(
        fetch=fetch,
        inventory_url="http://fixture/inventory",
        storage_url="http://fixture/storage",
        network_url="http://fixture/network",
        ollama_url="http://fixture/ollama",
    )
    return build_snapshot({"network": sources.network}, previous, now=now, ttl=300)


def test_success_counts_and_date_belong_to_same_discovery(sandbox, monkeypatch):
    scanner(monkeypatch, hosts=["192.168.2.20", "192.168.2.21"])
    discovered = discovery.discover()
    signal = result.read_result(discovery.RESULT_FILE)
    feed = collector().network()
    assert signal["status"] == "success"
    assert signal["attempt_id"] == signal["last_success"]["attempt_id"]
    assert feed["observed_at"] == discovered["observed_at"] == signal["finished_at"]
    assert (feed["total"], feed["online"], feed["missing"]) == (2, 2, 0)
    assert "192.168" not in discovery.RESULT_FILE.read_text()


def test_success_empty_is_available_and_can_mark_previous_devices_missing(sandbox, monkeypatch):
    scanner(monkeypatch, hosts=["192.168.2.20"])
    discovery.discover()
    sandbox[1][0] += timedelta(seconds=60)
    scanner(monkeypatch, hosts=[])
    discovery.discover()
    feed = collector().network()
    assert (feed["total"], feed["online"], feed["missing"]) == (1, 0, 1)
    assert feed["observed_at"] == "2026-10-08T14:01:00Z"
    assert result.read_result(discovery.RESULT_FILE)["status"] == "success"


def test_success_empty_without_history(sandbox, monkeypatch):
    scanner(monkeypatch, hosts=[])
    discovery.discover()
    assert collector().network()["total"] == 0


@pytest.mark.parametrize(
    "failure,status",
    [
        ("timeout", "timeout"),
        ("execution_error", "execution_error"),
        ("missing", "execution_error"),
        ("parsing_error", "parsing_error"),
    ],
)
def test_failures_preserve_inventory_date_snapshot_and_recover(
    sandbox, monkeypatch, failure, status
):
    scanner(monkeypatch, hosts=["192.168.2.20"])
    discovery.discover()
    old_inventory = discovery.INVENTORY_FILE.read_bytes()
    old_result = result.read_result(discovery.RESULT_FILE)
    old_snapshot = snapshot_from_feed(collector().network())
    sandbox[1][0] += timedelta(seconds=60)
    scanner(monkeypatch, failure=failure)
    with pytest.raises(discovery.DiscoveryFailure) as exc:
        discovery.discover()
    assert exc.value.status == status
    failed = result.read_result(discovery.RESULT_FILE)
    assert failed["status"] == status
    assert failed["last_success"] == old_result["last_success"]
    assert failed["attempt_id"] != old_result["attempt_id"]
    assert discovery.INVENTORY_FILE.read_bytes() == old_inventory
    with pytest.raises(ValueError, match="unavailable"):
        collector().network()
    failed_snapshot = snapshot_from_feed(None, old_snapshot, sandbox[1][0])
    assert failed_snapshot["network_devices"] == old_snapshot["network_devices"]
    assert failed_snapshot["sources"]["network"] == {
        "available": False,
        "observed_at": old_snapshot["sources"]["network"]["observed_at"],
    }
    assert failed_snapshot["ttl_seconds"] == 300
    assert freshness(failed_snapshot, sandbox[1][0], "network")["state"] == "stale"
    sandbox[1][0] += timedelta(seconds=60)
    scanner(monkeypatch, hosts=["192.168.2.20", "192.168.2.21"])
    discovery.discover()
    recovered = snapshot_from_feed(collector().network(), failed_snapshot, sandbox[1][0])
    assert recovered["network_devices"]["online"] == 2
    assert recovered["sources"]["network"]["observed_at"] == "2026-10-08T14:02:00Z"
    assert freshness(recovered, sandbox[1][0], "network")["state"] == "fresh"
    assert (
        freshness(recovered, sandbox[1][0] + timedelta(seconds=301), "network")["state"] == "stale"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Nmap scan report for invalid\nNmap done: 256 IP addresses (1 host up) scanned in 1.00 seconds",
        output(["192.168.2.20"]).replace("(1 hosts up)", "(2 hosts up)"),
        output(["192.168.2.20", "192.168.2.20"]),
        "unparseable content\n" + output([]),
        output(["192.168.2.20"]).replace("Host is up (0.001s latency).\n", ""),
    ],
)
def test_invalid_parsing_never_returns_empty_success(raw):
    with pytest.raises(discovery.DiscoveryFailure) as exc:
        discovery._validated_parse(raw, discovery.parse_quick_scan)
    assert exc.value.status == "parsing_error"


def test_running_attempt_and_missing_or_invalid_signal_fail_closed(sandbox, monkeypatch):
    with pytest.raises(FileNotFoundError):
        collector().network()

    scanner(monkeypatch, hosts=["192.168.2.20"])
    discovery.discover()
    old = result.read_result(discovery.RESULT_FILE)
    running = {**old, "attempt_id": str(uuid4()), "status": "running", "finished_at": None}
    result.write_result(discovery.RESULT_FILE, running)
    with pytest.raises(ValueError, match="unavailable"):
        collector().network()
    discovery.RESULT_FILE.write_text('{"status":"success"}')
    with pytest.raises(ValueError):
        collector().network()


def test_attempt_lock_refuses_overlap(sandbox):
    with (
        result.discovery_lock(discovery.RESULT_FILE),
        pytest.raises(OSError),
        result.discovery_lock(discovery.RESULT_FILE),
    ):
        pytest.fail("Overlapping attempt acquired the writer lock")


@pytest.mark.parametrize("change", ["identity", "date", "counts", "extra", "failed_renewal"])
def test_signal_cannot_mix_generations_or_renew_on_failure(sandbox, monkeypatch, change):
    scanner(monkeypatch, hosts=["192.168.2.20"])
    discovery.discover()
    signal = copy.deepcopy(result.read_result(discovery.RESULT_FILE))
    if change == "identity":
        signal["last_success"]["attempt_id"] = str(uuid4())
    elif change == "date":
        signal["last_success"]["observed_at"] = "2026-10-08T13:59:00Z"
    elif change == "counts":
        signal["last_success"]["online"] = 2
    elif change == "extra":
        signal["last_success"]["ip"] = "private"
    else:
        signal["status"] = "timeout"
    with pytest.raises(ValueError):
        result.validate_result(signal)


def test_failed_discovery_http_503_then_recovery_200(sandbox, monkeypatch):
    collectors = collector()
    store = source.SourceStore()
    scanner(monkeypatch, hosts=["192.168.2.20"])
    discovery.discover()
    store.refresh(collectors)
    server = source.SourceServer(("127.0.0.1", 0), store)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()

    def request():
        conn = http.client.HTTPConnection(*server.server_address, timeout=2)
        conn.request("GET", "/network")
        response = conn.getresponse()
        answer = response.status, response.read()
        conn.close()
        return answer

    try:
        assert request()[0] == 200
        base = f"http://{server.server_address[0]}:{server.server_address[1]}"
        transport = ReadOnlyHTTP(allowed_urls=[base + "/network"])
        sources = Sources(
            fetch=transport.get,
            inventory_url="http://unused/inventory",
            storage_url="http://unused/storage",
            network_url=base + "/network",
            ollama_url="http://unused/ollama",
        )
        previous = build_snapshot({"network": sources.network}, now=START, ttl=300)
        scanner(monkeypatch, failure="timeout")
        with pytest.raises(discovery.DiscoveryFailure):
            discovery.discover()
        store.refresh(collectors)
        assert request() == (503, b'{"error":"source unavailable"}')
        failed = build_snapshot(
            {"network": sources.network}, previous, now=START + timedelta(seconds=60), ttl=300
        )
        assert failed["sources"]["network"]["available"] is False
        assert (
            failed["sources"]["network"]["observed_at"]
            == previous["sources"]["network"]["observed_at"]
        )
        assert failed["network_devices"] == previous["network_devices"]
        sandbox[1][0] += timedelta(seconds=120)
        scanner(monkeypatch, hosts=[])
        discovery.discover()
        store.refresh(collectors)
        assert request()[0] == 200
        recovered = build_snapshot({"network": sources.network}, failed, now=sandbox[1][0], ttl=300)
        assert recovered["sources"]["network"]["available"] is True
        assert recovered["sources"]["network"]["observed_at"] == "2026-10-08T14:02:00Z"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_runtime_failure_does_not_renew_legacy_timestamp(sandbox, monkeypatch):
    """Exercise the actual runtime function without importing unrelated live integrations."""
    tree = ast.parse((Path(__file__).parents[1] / "workspace/runtime/runtime.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_network_scan_without_cadence_guard"
    )
    stamp = sandbox[0] / "legacy_timestamp"
    stamp.write_text("123")
    namespace = {
        "__package__": "workspace.runtime",
        "time": SimpleNamespace(time=lambda: 10000),
        "NMAP_CACHE_FILE": sandbox[0] / "nmap.json",
        "NMAP_INTERVAL_SECONDS": 1800,
        "load_json": lambda *args: {"last_scan": 0},
        "save_json": lambda *args: None,
        "executors": SimpleNamespace(check_network_devices=lambda: {"count": 37}),
        "freeze_detector": SimpleNamespace(heartbeat=lambda *args: None),
        "Path": lambda _: stamp,
        "datetime": datetime,
        "_log": lambda _: None,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "runtime.py", "exec"), namespace)
    scanner(monkeypatch, failure="timeout")
    namespace["_network_scan_without_cadence_guard"]({})
    assert stamp.read_text() == "123"
    scanner(monkeypatch, hosts=["192.168.2.20"])
    namespace["_network_scan_without_cadence_guard"]({})
    assert float(stamp.read_text()) == START.timestamp()


@pytest.mark.parametrize("failure", ["timeout", "execution_error", "missing"])
def test_failure_logging_contains_only_category(sandbox, monkeypatch, failure):
    entries = []
    monkeypatch.setattr(discovery, "_log", entries.append)
    scanner(monkeypatch, failure=failure)
    with pytest.raises(discovery.DiscoveryFailure):
        discovery.discover()
    assert "private" not in json.dumps(entries)


@pytest.mark.parametrize("content", [b'{"status":"success","status":"timeout"}', b"x" * 4097])
def test_ambiguous_or_oversized_signal_is_unavailable(sandbox, content):
    discovery.RESULT_FILE.write_bytes(content)
    with pytest.raises(ValueError):
        collector().network()


@pytest.mark.skipif(os.name != "posix", reason="Linux permission contract")
def test_linux_public_signal_private_lock_and_atomic_inventory(sandbox, monkeypatch):
    import stat

    scanner(monkeypatch, hosts=["192.168.2.20"])
    discovery.discover()
    assert stat.S_IMODE(discovery.RESULT_FILE.stat().st_mode) == 0o644
    assert stat.S_IMODE(Path(str(discovery.RESULT_FILE) + ".lock").stat().st_mode) == 0o600
    assert stat.S_IMODE(discovery.INVENTORY_FILE.stat().st_mode) == 0o600
