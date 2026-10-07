"""Local deployment contract tests; never invoke a VM or privileged helper."""

import json
import re
import shlex
from pathlib import Path

import pytest
from tests.test_mcp_snapshot_producer import producer_config
from tools.mcp_v2_helper_wiring import CACHE, RELEASE, gateway_dropin, prepare_plan
from workspace.runtime.mcp_snapshot_producer import configured_sources

ROOT = Path(__file__).resolve().parents[1]
BASELINE = [
    "/usr/bin/python3",
    "-m",
    "workspace.runtime.mcp_readonly_gateway",
    "--token-file",
    "/opt/tenazas-mcp/secrets/mcp-internal-token",
    "--cert",
    "/opt/tenazas-mcp/tls/mcp.crt",
    "--key",
    "/opt/tenazas-mcp/tls/mcp.key",
]


def unit_fields(path):
    fields = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            fields.setdefault(key, []).append(value)
    return fields


def test_producer_service_runtime_and_only_cache_write_access():
    fields = unit_fields(ROOT / "deploy/mcp-v2/tenazas-mcp-snapshot.service")
    assert fields["WorkingDirectory"] == [RELEASE]
    args = shlex.split(fields["ExecStart"][0])
    assert args == [
        "/usr/bin/python3",
        "-m",
        "workspace.runtime.mcp_snapshot_producer",
        "--cache-root",
        CACHE,
        "--config-file",
        "/etc/tenazas-mcp-v2/producer.json",
    ]
    assert fields["ReadWritePaths"] == [CACHE]
    assert set(fields["ReadOnlyPaths"][0].split()) == {"/tmp", "/var/tmp", "/dev/shm"}
    assert fields["ProtectSystem"] == ["strict"]
    assert fields["NoNewPrivileges"] == ["true"]
    assert fields["UMask"] == ["0077"]
    assert fields["User"] == fields["Group"] == ["tenazas-mcp"]
    assert "Environment" not in fields and "EnvironmentFile" not in fields


@pytest.mark.parametrize(
    "old_cache", [[], ["--cache-root", "/old/cache"], ["--cache-root=/old/cache"]]
)
def test_gateway_arguments_preserved_and_cache_read_only(old_cache):
    rendered = gateway_dropin(BASELINE + old_cache)
    lines = rendered.splitlines()
    start = next(
        line[len("ExecStart=") :]
        for line in lines
        if line.startswith("ExecStart=") and line != "ExecStart="
    )
    assert shlex.split(start) == BASELINE + ["--cache-root", CACHE]
    assert f"WorkingDirectory={RELEASE}" in lines
    assert "ReadWritePaths=" in lines
    assert f"ReadOnlyPaths={CACHE}" in lines
    assert "ProtectSystem=strict" in lines and "NoNewPrivileges=true" in lines


def test_reviewed_gateway_dropin_matches_baseline_renderer():
    fields = unit_fields(ROOT / "deploy/mcp-v2/tenazas-mcp-gateway.conf")
    assert fields["ExecStart"][0] == ""
    assert shlex.split(fields["ExecStart"][1]) == BASELINE + ["--cache-root", CACHE]
    assert fields["ReadWritePaths"] == [""]
    assert fields["ReadOnlyPaths"] == [CACHE]


def test_preserve_additional_baseline_gateway_arguments():
    argv = BASELINE + ["--port", "8443"]
    start = [line for line in gateway_dropin(argv).splitlines() if line.startswith("ExecStart=")][
        -1
    ]
    assert shlex.split(start.split("=", 1)[1]) == argv + ["--cache-root", CACHE]


@pytest.mark.parametrize(
    "bad", [BASELINE[:3], ["/bin/sh", "-c", "private"], BASELINE + ["$PRIVATE"], BASELINE + ["%n"]]
)
def test_unsafe_baseline_rejected(bad):
    with pytest.raises(ValueError):
        gateway_dropin(bad)


def test_prepare_requires_real_config_before_any_plan():
    with pytest.raises(ValueError):
        prepare_plan({})
    plan = prepare_plan(producer_config())  # Fictional unit-test inputs only.
    assert plan["cwd"] == RELEASE
    provision, initialize, validate = plan["commands"]
    assert provision[-3:] == ("-m", "0700", CACHE)
    assert initialize[-3:] == ("--cache-root", CACHE, "--initialize")
    assert "--config-file" not in initialize
    assert initialize[:7] == (
        "/usr/sbin/runuser",
        "-u",
        "tenazas-mcp",
        "-g",
        "tenazas-mcp",
        "--",
        "/usr/bin/python3",
    )
    assert "validate_snapshot" in validate[-1] and "validate_cache" in validate[-1]
    assert plan["preserve_old_cache"] == "/opt/tenazas-mcp/app/workspace/runtime/mcp-cache"
    assert plan["rollback_remove_only"].endswith("tenazas-mcp.service.d/mcp-v2-staging.conf")


def test_schema_documents_exact_runtime_fields_and_bounds():
    schema = json.loads((ROOT / "deploy/mcp-v2/producer.schema.json").read_text())
    config = producer_config()
    assert set(schema["required"]) == set(schema["properties"]) == set(config)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["ttl_seconds"]["maximum"] == 3600
    assert schema["properties"]["timeout_seconds"]["maximum"] == 30
    configured_sources(config)  # Normative validation; no fetch performed.
    pattern = schema["$defs"]["source_url"]["pattern"]
    assert re.fullmatch(pattern, config["inventory_url"])
    assert not re.fullmatch(pattern, "http://fixture/with space")


@pytest.mark.parametrize(
    "missing",
    [
        "inventory_url",
        "storage_url",
        "network_url",
        "ollama_url",
        "ttl_seconds",
        "timeout_seconds",
    ],
)
def test_missing_configuration_blocks_prepare(missing):
    config = producer_config()
    del config[missing]
    with pytest.raises(ValueError):
        prepare_plan(config)
