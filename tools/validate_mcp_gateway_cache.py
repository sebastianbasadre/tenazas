"""Unprivileged gateway-role probe, without raw cache or credential inventory."""

import json
import os
from pathlib import Path

from workspace.runtime import mcp_readonly_adapter as adapter


def inaccessible(path):
    try:
        return not Path(path).exists()
    except PermissionError:
        return True


def main():
    assert os.getuid() == 1026
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/home/tenazas/.ssh").exists()
    assert inaccessible("/root/.ssh")
    assert not Path("/source-state").exists()
    assert not Path("/home/tenazas/.openclaw").exists()
    assert not any(marker in key.lower() for key in os.environ for marker in
                   ("token", "password", "secret", "api_key", "authorization", "cookie"))
    state = adapter._state()
    assert state.get("containers"), "Real exported cache was not readable"
    files = list(adapter.CACHE_ROOT.rglob("*.json"))
    assert files and all(not os.access(path, os.W_OK) for path in files)
    outputs = {name: adapter.call_tool(name) for name in adapter.TOOLS}
    status = Path("/proc/self/status").read_text()
    assert "CapEff:\t0000000000000000" in status
    if "NoNewPrivs:" in status:
        assert "NoNewPrivs:\t1" in status
    else:
        # Older NAS kernels omit this /proc field; query the read-only prctl API.
        import ctypes
        assert ctypes.CDLL(None).prctl(39, 0, 0, 0, 0) == 1
    interfaces = {line.split()[0] for line in Path("/proc/net/route").read_text().splitlines()[1:]}
    assert not interfaces - {"lo"}
    report = {"uid": os.getuid(), "gid": os.getgid(), "tools": list(outputs),
                      "containers": outputs["get_containers"]["total"],
                      "cache_readable": True, "cache_read_only": True,
                      "no_docker_socket": True, "no_ssh_keys": True,
                      "no_raw_cache_or_credential_mounts": True,
                      "no_credential_environment": True, "capabilities_zero": True,
                      "no_new_privileges": True, "network_none": True}
    if os.getenv("MCP_VALIDATE_INCLUDE_OUTPUT") == "1":
        report["_output_for_client_audit"] = outputs
    print(json.dumps(report))


if __name__ == "__main__":
    main()
