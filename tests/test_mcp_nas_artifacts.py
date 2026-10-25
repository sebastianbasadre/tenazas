import json
import shlex
import tarfile
from pathlib import Path

import pytest
from tools.mcp_build_packages import PAYLOADS, build
from workspace.runtime import mcp_sanitized_sources as source
from workspace.runtime.mcp_source_contract import storage_payload

ROOT = Path(__file__).resolve().parents[1]


def test_validated_volume_conversion():
    data = storage_payload(28770439729152, 12026896465920, "2026-10-06T00:00:00Z")
    assert data["total_gb"] == 28770.439729152
    assert data["free_gb"] == 16743.543263232
    assert data["used_gb"] == 12026.89646592


def test_network_uses_discovery_signal_without_exporter_fallback():
    text = (ROOT / "workspace/runtime/mcp_sanitized_sources.py").read_text()
    assert "read_result(self.network_result_file)" in text
    assert "9105" not in text and "9108" not in text
    assert "device_inventory.json" not in text and "last_seen" not in text
    assert not hasattr(source, "fetch_network_metrics")


def test_independent_nas_service_bind_port_and_identity():
    fields = {}
    for line in (ROOT / "deploy/mcp-v2/tenazas-mcp-sources.service").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            fields[k] = v
    args = shlex.split(fields["ExecStart"])
    assert args[0] == "/usr/local/bin/python3.13"
    assert args[args.index("--bind") + 1] == "192.168.2.2"
    assert args[args.index("--port") + 1] == "19105"
    assert args[args.index("--filesystem") + 1] == "/volume1"
    assert args[args.index("--network-result-file") + 1] == (
        "/volume1/docker/openclaw/workspace/runtime/network_discovery_result.json"
    )
    assert fields["User"] == "sbasadre" and fields["Group"] == "docker"
    assert "EnvironmentFile" not in fields and "ReadWritePaths" not in fields


def test_dsm_219_directives_without_seccomp_or_boot_enablement():
    text = (ROOT / "deploy/mcp-v2/tenazas-mcp-sources.service").read_text()
    fields = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#")
    )
    supported = {
        "Description",
        "Type",
        "User",
        "Group",
        "WorkingDirectory",
        "ExecStart",
        "UMask",
        "NoNewPrivileges",
        "ProtectSystem",
        "ProtectHome",
        "PrivateTmp",
        "ReadOnlyDirectories",
        "CapabilityBoundingSet",
        "Restart",
    }
    assert set(fields) == supported
    assert fields["ProtectSystem"] == "full"
    assert set(fields["ReadOnlyDirectories"].split()) == {"/", "/tmp", "/var/tmp", "/dev/shm"}
    assert fields["NoNewPrivileges"] == fields["ProtectHome"] == fields["PrivateTmp"] == "true"
    assert fields["CapabilityBoundingSet"] == ""
    assert fields["UMask"] == "0077"
    assert "[Install]" not in [
        line.strip() for line in text.splitlines() if not line.startswith("#")
    ]
    assert "RestrictAddressFamilies" not in fields and "ReadOnlyPaths" not in fields


@pytest.mark.parametrize("kind", PAYLOADS)
def test_packages_are_separate_complete_and_source_only(tmp_path, kind):
    archive, manifest = build(ROOT, tmp_path, kind)
    data = json.loads(manifest.read_text())
    with tarfile.open(archive) as output:
        names = output.getnames()
        normalized = {name[4:] if name.startswith("app/") else name for name in names}
        assert normalized == set(PAYLOADS[kind]) == set(data["files"])
        assert all(member.isfile() for member in output)
    assert all(not name.endswith((".env", ".key", ".pem")) for name in names)
    if kind == "mcp-v2":
        assert "tools/mcp_nas_inventory.py" not in names
        assert "workspace/runtime/mcp_sanitized_sources.py" not in names
    with pytest.raises(ValueError):
        build(ROOT, tmp_path, kind)


def test_readonly_preflight_does_not_launch_or_use_sudo():
    text = (ROOT / "tools/mcp_nas_preflight.py").read_text()
    assert "4822" in text and "/usr/local/bin/docker" in text
    assert "bind(" not in text and "systemctl" not in text and "sudo" not in text
