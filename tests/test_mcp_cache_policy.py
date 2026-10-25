"""Linux ownership, mode, link and atomic-publication verification."""

import json
import os
import sys
from pathlib import Path

import pytest
from tests.test_mcp_snapshot_producer import snapshot
from workspace.runtime import mcp_cache_policy as policy
from workspace.runtime import mcp_snapshot_producer as producer

pytestmark = [
    pytest.mark.linux_filesystem,
    pytest.mark.skipif(
        sys.platform != "linux", reason="Requires Linux UID/GID, modes and fsync semantics"
    ),
]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    # Production resolves the dedicated account. Linux fixtures use their own account.
    monkeypatch.setattr(policy, "expected_identity", lambda: (os.geteuid(), os.getegid()))
    root = tmp_path / "cache"
    root.mkdir(mode=0o700)
    (root / "incidents").mkdir(mode=0o700)
    for name, data in (("state.json", snapshot()), ("infra.json", {})):
        path = root / name
        path.write_text(json.dumps(data))
        path.chmod(0o600)
    return root


def test_valid_cache(cache):
    policy.validate_cache(cache)


@pytest.mark.parametrize(
    "violation",
    ["mode", "owner", "group", "symlink", "hardlink", "size", "layout", "directory", "incidents"],
)
def test_cache_rejects(cache, monkeypatch, violation):
    state = cache / "state.json"
    if violation == "mode":
        state.chmod(0o644)
    elif violation == "owner":
        monkeypatch.setattr(policy, "expected_identity", lambda: (os.geteuid() + 1, os.getegid()))
    elif violation == "group":
        monkeypatch.setattr(policy, "expected_identity", lambda: (os.geteuid(), os.getegid() + 1))
    elif violation == "symlink":
        state.rename(cache.parent / "other.json")
        state.symlink_to(cache.parent / "other.json")
    elif violation == "hardlink":
        os.link(state, cache.parent / "other.json")
    elif violation == "size":
        state.write_bytes(b"x" * (policy.MAX_BYTES + 1))
    elif violation == "layout":
        (cache / "private").write_text("fixture")
    elif violation == "directory":
        state.unlink()
        state.mkdir()
    elif violation == "incidents":
        (cache / "incidents" / "alert-00.json").write_text("{}")
    with pytest.raises(ValueError):
        policy.validate_cache(cache)


def test_atomic_replacement(cache, monkeypatch):
    old = json.loads((cache / "state.json").read_text())
    new = snapshot()
    replace = producer.os.replace
    seen = []

    def observe(source, destination):
        assert Path(source).parent == Path(destination).parent
        assert json.loads(Path(destination).read_text()) == old
        assert json.loads(Path(source).read_text()) == new
        policy.validate_cache(cache)  # Readers work while the temporary file exists.
        seen.append(True)
        replace(source, destination)

    monkeypatch.setattr(producer.os, "replace", observe)
    producer.atomic_replace(cache, new)
    assert seen and json.loads((cache / "state.json").read_text()) == new
    assert (cache / "state.json").stat().st_mode & 0o777 == 0o600
    policy.validate_cache(cache)


def test_replace_failure_preserves_old(cache, monkeypatch):
    old = (cache / "state.json").read_bytes()

    def fail(*_):
        raise OSError("fixture failure")

    monkeypatch.setattr(producer.os, "replace", fail)
    with pytest.raises(OSError):
        producer.atomic_replace(cache, snapshot())
    assert (cache / "state.json").read_bytes() == old
    policy.validate_cache(cache)


def test_invalid_schema_never_replaces(cache):
    old = (cache / "state.json").read_bytes()
    with pytest.raises(ValueError):
        producer.atomic_replace(cache, {"token": "private"})
    assert (cache / "state.json").read_bytes() == old


def test_descriptor_owner_checked(cache, monkeypatch):
    from workspace.runtime import mcp_readonly_adapter as adapter

    monkeypatch.setattr(adapter, "STATE_FILE", cache / "state.json")
    assert adapter._state()["generation"]
    monkeypatch.setattr(policy, "expected_identity", lambda: (os.geteuid(), os.getegid() + 1))
    with pytest.raises(ValueError):
        adapter._state()


def test_fsync_failure_preserves_old(cache, monkeypatch):
    old = (cache / "state.json").read_bytes()

    def fail(*_):
        raise OSError("fixture failure")

    monkeypatch.setattr(producer.os, "fsync", fail)
    with pytest.raises(OSError):
        producer.atomic_replace(cache, snapshot())
    assert (cache / "state.json").read_bytes() == old
    policy.validate_cache(cache)


def test_producer_identity_rejected(cache, monkeypatch):
    old = (cache / "state.json").read_bytes()
    monkeypatch.setattr(producer.os, "getegid", lambda: -1)
    # Freeze the expected identity independently of the process identity probe.
    gid = (cache / "state.json").stat().st_gid
    monkeypatch.setattr(policy, "expected_identity", lambda: (os.geteuid(), gid))
    with pytest.raises(ValueError):
        producer.atomic_replace(cache, snapshot())
    assert (cache / "state.json").read_bytes() == old


def test_real_token_loading(cache):
    from workspace.runtime.mcp_readonly_gateway import load_token

    token = cache.parent / "credential-fixture"
    token.write_text("T" * 48)
    token.chmod(0o600)
    assert load_token(token) == "T" * 48
    token.chmod(0o644)
    with pytest.raises(ValueError):
        load_token(token)


def test_reader_rejects_symlink(cache, monkeypatch):
    from workspace.runtime import mcp_readonly_adapter as adapter

    state = cache / "state.json"
    state.rename(cache.parent / "other.json")
    state.symlink_to(cache.parent / "other.json")
    monkeypatch.setattr(adapter, "STATE_FILE", state)
    with pytest.raises(ValueError):
        adapter._state()


def test_produce_cycle(cache):
    class OfflineSources:
        def containers(self):
            return {"containers": []}

        def models(self):
            raise OSError("private-source-address")

        def storage(self):
            return {"disk": {"free_gb": 9}}

        def network(self):
            return {"internet": {}, "vpn": {}, "network_devices": {"total": 2}}

    generation = producer.produce_once(cache, OfflineSources())
    data = json.loads((cache / "state.json").read_text())
    assert data["generation"] == generation
    assert data["sources"]["models"]["available"] is False
    assert "private-source" not in json.dumps(data)


def test_initialize_cache_and_refuse_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "expected_identity", lambda: (os.geteuid(), os.getegid()))
    root = tmp_path / "empty"
    root.mkdir(mode=0o700)
    producer.initialize_cache(root)
    policy.validate_cache(root)
    data = json.loads((root / "state.json").read_text())
    from workspace.runtime.mcp_snapshot_contract import freshness, validate_snapshot

    validate_snapshot(data)
    assert data["schema_version"] == 1
    assert data["generation"] and data["generated_at"].endswith("Z")
    assert freshness(data)["state"] == "stale"
    assert all(not s["available"] for s in data["sources"].values())
    assert (root / "state.json").stat().st_mode & 0o777 == 0o600
    old = (root / "state.json").read_bytes()
    with pytest.raises(ValueError):
        producer.initialize_cache(root)
    assert (root / "state.json").read_bytes() == old


def test_gateway_main_explicit_cache_root(cache, monkeypatch):
    import sys

    from workspace.runtime import mcp_readonly_adapter as adapter
    from workspace.runtime import mcp_readonly_gateway as gateway

    # Restore globals after the startup-only configure_cache assignment.
    monkeypatch.setattr(adapter, "CACHE_ROOT", cache.parent / "not-the-cache")
    monkeypatch.setattr(adapter, "STATE_FILE", cache.parent / "not-the-state")
    seen = []

    class Server:
        def __init__(self, address, token_file, **kwargs):
            assert cache == adapter.CACHE_ROOT and cache / "state.json" == adapter.STATE_FILE
            seen.append(adapter.get_status()["snapshot"]["generation"])

        def serve_forever(self):
            pass

    class TLS:
        def __init__(self, *_):
            pass

        def load_cert_chain(self, *_):
            pass

    monkeypatch.setattr(gateway, "Gateway", Server)
    monkeypatch.setattr(gateway.ssl, "SSLContext", TLS)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gateway",
            "--cache-root",
            str(cache),
            "--token-file",
            "fixture",
            "--cert",
            "fixture",
            "--key",
            "fixture",
        ],
    )
    gateway.main()
    assert seen


def test_single_writer_excludes_second_before_collection(cache):
    class Sources:
        def __getattr__(self, _):
            raise AssertionError("Concurrent collector must never run")

    old = (cache / "state.json").read_bytes()
    with producer.writer_lock(cache), pytest.raises(producer.ProducerBusy):
        producer.produce_once(cache, Sources())
    assert (cache / "state.json").read_bytes() == old
    assert (cache / ".producer.lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("bad", ["symlink", "hardlink", "mode"])
def test_lock_policy_rejects(cache, bad):
    lock = cache / ".producer.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    if bad == "symlink":
        lock.unlink()
        lock.symlink_to(cache / "state.json")
    elif bad == "hardlink":
        os.link(lock, cache.parent / "lock-copy")
    else:
        lock.chmod(0o644)
    with pytest.raises((ValueError, OSError)), producer.writer_lock(cache):
        pass


def test_file_fsync_failure_preserves_old(cache, monkeypatch):
    import stat

    old = (cache / "state.json").read_bytes()
    fsync = producer.os.fsync

    def fail_file(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("fixture failure")
        fsync(fd)

    monkeypatch.setattr(producer.os, "fsync", fail_file)
    with pytest.raises(OSError):
        producer.atomic_replace(cache, snapshot())
    assert (cache / "state.json").read_bytes() == old
    assert not list(cache.glob(".state-*.tmp"))


def test_concurrent_reader_gets_whole_generation(cache, monkeypatch):
    import threading

    from workspace.runtime import mcp_readonly_adapter as adapter

    monkeypatch.setattr(adapter, "STATE_FILE", cache / "state.json")
    start, done = threading.Event(), threading.Event()
    errors, generations = [], []

    def reader():
        start.set()
        while not done.is_set():
            try:
                policy.validate_cache(cache)
                value = adapter.call_tool("get_status")
                g = value["snapshot"]["generation"]
                assert all(
                    value[key]["snapshot"]["generation"] == g
                    for key in ("containers", "models", "storage", "network", "alerts")
                )
                generations.append(g)
            except Exception as error:
                errors.append(type(error).__name__)

    thread = threading.Thread(target=reader)
    thread.start()
    start.wait(timeout=2)
    try:
        for _ in range(20):
            producer.atomic_replace(cache, snapshot())
    finally:
        done.set()
        thread.join(timeout=2)
    assert generations and not errors and not thread.is_alive()


@pytest.mark.parametrize("bad", ["missing", "malformed", "oversized"])
def test_snapshot_read_fail_closed(cache, monkeypatch, bad):
    from workspace.runtime import mcp_readonly_adapter as adapter

    path = cache / "state.json"
    monkeypatch.setattr(adapter, "STATE_FILE", path)
    if bad == "missing":
        path.unlink()
    elif bad == "malformed":
        path.write_bytes(b"{")
    else:
        path.write_bytes(b"x" * (policy.MAX_BYTES + 1))
    with pytest.raises(ValueError):
        adapter.call_tool("get_status")


def test_reader_retries_unlinked_descriptor(cache, monkeypatch):
    from workspace.runtime import mcp_readonly_adapter as adapter

    monkeypatch.setattr(adapter, "STATE_FILE", cache / "state.json")
    opener = adapter._open_snapshot
    replacement = snapshot()
    fired = []

    def rename_after_open(path):
        stream = opener(path)
        if not fired:
            fired.append(True)
            producer.atomic_replace(cache, replacement)
        return stream

    monkeypatch.setattr(adapter, "_open_snapshot", rename_after_open)
    assert adapter._state()["generation"] == replacement["generation"]


def test_cli_bootstrap_and_publish_offline(cache, monkeypatch, caplog):
    from tests.test_mcp_snapshot_producer import producer_config

    class OfflineSources:
        def containers(self):
            return {"containers": []}

        def models(self):
            return {"ollama_notebook": {}, "ollama_nas": {}, "model": {}}

        def storage(self):
            return {"disk": {"free_gb": 9}}

        def network(self):
            return {"internet": {}, "vpn": {}, "network_devices": {"total": 2}}

    monkeypatch.setattr(producer, "configured_sources", lambda _: (OfflineSources(), 300))
    config = cache.parent / "fixture-producer.json"
    config.write_text(json.dumps(producer_config()))
    before = json.loads((cache / "state.json").read_text())["generation"]
    assert producer.main(["--cache-root", str(cache), "--config-file", str(config)]) == 0
    assert json.loads((cache / "state.json").read_text())["generation"] != before
    with producer.writer_lock(cache):
        assert producer.main(["--cache-root", str(cache), "--config-file", str(config)]) == 2
    policy.validate_cache(cache)


@pytest.mark.parametrize("bad", [None, "missing", "malformed", "oversized"])
def test_gateway_real_cache_boundary(cache, monkeypatch, bad):
    import http.client
    import threading

    from workspace.runtime import mcp_readonly_adapter as adapter
    from workspace.runtime import mcp_readonly_gateway as gateway

    monkeypatch.setattr(adapter, "CACHE_ROOT", cache)
    monkeypatch.setattr(adapter, "STATE_FILE", cache / "state.json")
    token = cache.parent / "gateway-fixture-token"
    token.write_text("T" * 48)
    token.chmod(0o600)
    if bad == "missing":
        (cache / "state.json").unlink()
    elif bad == "malformed":
        (cache / "state.json").write_text('{"credential":"private-fixture-value"}')
    elif bad == "oversized":
        (cache / "state.json").write_bytes(b"x" * (policy.MAX_BYTES + 1))
    server = gateway.Gateway(("127.0.0.1", 0), token)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        connection.request(
            "POST",
            "/mcp",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "fixture",
                    "method": "tools/call",
                    "params": {"name": "get_status", "arguments": {}},
                }
            ),
            {"Authorization": "Bearer " + "T" * 48, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        assert response.status == 200 and b"private-fixture" not in raw
        payload = json.loads(raw)
        if bad:
            assert payload["error"] == {"code": -32603, "message": "Read unavailable"}
        else:
            value = json.loads(payload["result"]["content"][0]["text"])
            assert value["snapshot"]["generation"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
