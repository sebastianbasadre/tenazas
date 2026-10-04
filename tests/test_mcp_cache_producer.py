import json
import os
import stat

import pytest

from workspace.runtime import mcp_cache_producer as producer
from workspace.runtime import mcp_readonly_adapter as adapter


def test_secrets_are_removed_before_adapter_receives_cache(tmp_path, monkeypatch):
    secret = "shortpw!"
    raw = {"token": secret, "containers": [{"name": secret, "status": secret, "up": True}],
           "model": {"name": secret, "unknown_blob": secret}, "vpn": {"ip": secret},
           "internet": {"ok": True, "headers": {"Authorization": secret}}}
    infra = {"sub": "disk", "raw": {"free_gb": 12, "name": secret}}
    incidents = [{"incident_id": secret, "title": secret, "severity": "critical", "status": "open"}]
    root = tmp_path / "cache"
    producer.export_cache(raw, infra, incidents, secret_values=[secret], destination=root)
    files = list(root.rglob("*.json"))
    assert len(files) == 3
    assert all(secret not in path.read_text() for path in files)
    monkeypatch.setattr(adapter, "STATE_FILE", root / "state.json")
    monkeypatch.setattr(adapter, "AGENT_STATUS_FILE", root / "infra.json")
    monkeypatch.setattr(adapter, "INCIDENTS_DIR", root / "incidents")
    assert secret not in json.dumps(adapter.get_status())
    assert adapter.get_containers()["up"] == 1
    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)


def test_inventory_is_mandatory_and_schema_collision_fails_closed(tmp_path):
    for invalid in (None, {}, "password", ["password", False]):
        with pytest.raises(ValueError):
            producer.export_cache({}, secret_values=invalid, destination=tmp_path / "cache")
    with pytest.raises(ValueError):
        producer.export_cache({}, secret_values=["containers"], destination=tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_stale_exported_incidents_are_removed(tmp_path):
    producer.export_cache({}, incidents=[{"incident_id": "one"}], secret_values=[], destination=tmp_path)
    producer.export_cache({}, secret_values=[], destination=tmp_path)
    assert not list((tmp_path / "incidents").glob("*.json"))


def test_adapter_reads_only_dedicated_cache_by_default():
    assert adapter.STATE_FILE.parent == adapter.CACHE_ROOT
    assert adapter.AGENT_STATUS_FILE.parent == adapter.CACHE_ROOT
    assert adapter.INCIDENTS_DIR.parent == adapter.CACHE_ROOT
    assert adapter.CACHE_ROOT != adapter.RUNTIME


def test_encoded_credentials_are_not_exported(tmp_path):
    secret = "shortpw!"
    variants = producer.credential_variants(secret)
    producer.export_cache({"models": {"models": list(variants)}},
                          secret_values=[secret], destination=tmp_path)
    encoded_cache = (tmp_path / "state.json").read_text()
    assert all(variant not in encoded_cache for variant in variants)


def test_credentials_are_redacted_before_truncation():
    secret = "!?" * 200
    result = producer.project_cache({"model": {"name": secret}}, secret_values=[secret])
    assert result["state"]["model"]["name"] == "[REDACTED]"
