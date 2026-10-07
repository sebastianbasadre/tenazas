"""Standalone read-only NAS preflight; stream via SSH port 4822, no SFTP/root.

No payloads, container names, environment or credentials are printed. No listener
is opened; the proposed source port is checked via kernel tables, not a bind.
"""

import datetime
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request

SOURCE_PORT = 19105
METRICS = {
    "tenazas_network_last_scan_timestamp",
    "device_discovery_online",
    "device_discovery_missing",
}


def preflight():
    result = {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "runtime_supported": sys.version_info >= (3, 11),
        "root_required": False,
    }
    volume = pathlib.Path("/volume1")
    try:
        safe = volume.resolve(strict=True) == volume and os.path.ismount(volume)
        stats = os.statvfs(str(volume))
        identity = []
        for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split()
            if fields[4] == "/volume1":
                separator = fields.index("-")
                identity.append(
                    {"filesystem": fields[separator + 1], "device": fields[separator + 2]}
                )
        result["storage"] = {
            "confirmed": safe and any(i["filesystem"] == "btrfs" for i in identity),
            "path": "/volume1",
            "identity": identity,
            "total_bytes": stats.f_frsize * stats.f_blocks,
            "available_bytes": stats.f_frsize * stats.f_bavail,
        }
    except Exception:
        result["storage"] = {"confirmed": False, "error": "metadata_unavailable"}
    try:
        import grp

        response = subprocess.run(
            ["/usr/local/bin/docker", "ps", "-a", "--format", '{"State":{{json .State}}}'],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        rows = [json.loads(line) for line in response.stdout.splitlines()]
        result["docker"] = {
            "capable": True,
            "total": len(rows),
            "running": sum(row.get("State") == "running" for row in rows),
            "docker_group_member": "docker" in [grp.getgrgid(g).gr_name for g in os.getgroups()],
        }
    except Exception:
        result["docker"] = {"capable": False, "error": "inventory_unavailable"}
    try:

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open("http://192.168.2.2:9105/metrics", timeout=5) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError()
        samples = {}
        for line in raw.decode().splitlines():
            fields = line.split()
            if fields and fields[0] in METRICS:
                if len(fields) not in (2, 3) or fields[0] in samples:
                    raise ValueError()
                value = float(fields[1])
                if not math.isfinite(value) or value < 0:
                    raise ValueError()
                samples[fields[0]] = value
        if (
            set(samples) != METRICS
            or samples["tenazas_network_last_scan_timestamp"] <= 0
            or any(
                not samples[k].is_integer()
                for k in METRICS
                if k != "tenazas_network_last_scan_timestamp"
            )
        ):
            raise ValueError()
        stamp = (
            datetime.datetime.fromtimestamp(
                samples["tenazas_network_last_scan_timestamp"], datetime.UTC
            )
            .isoformat()
            .replace("+00:00", "Z")
        )
        result["network"] = {
            "confirmed": True,
            "observed_at": stamp,
            "online": int(samples["device_discovery_online"]),
            "missing": int(samples["device_discovery_missing"]),
            "timestamp_metric": "tenazas_network_last_scan_timestamp",
        }
    except Exception:
        result["network"] = {"confirmed": False, "error": "required_metrics_unavailable"}
    try:
        ports = set()
        for path in ("/proc/net/tcp", "/proc/net/tcp6"):
            for line in pathlib.Path(path).read_text().splitlines()[1:]:
                fields = line.split()
                ports.add(int(fields[1].split(":")[1], 16))
        result["source_port"] = {
            "proposed": SOURCE_PORT,
            "currently_unused": SOURCE_PORT not in ports,
            "reserved_or_bound": False,
        }
    except Exception:
        result["source_port"] = {"proposed": SOURCE_PORT, "currently_unused": None}
    try:
        ip = shutil.which("ip")
        if not ip:
            raise ValueError()
        addresses = subprocess.run(
            [ip, "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
        result["private_bind_available"] = any(
            field.split("/")[0] == "192.168.2.2" for field in addresses.split()
        )
    except Exception:
        result["private_bind_available"] = False
    result["pass"] = all(
        (
            result["runtime_supported"],
            result["storage"]["confirmed"],
            result["docker"]["capable"],
            result["network"]["confirmed"],
            result["source_port"].get("currently_unused") is True,
            result["private_bind_available"],
        )
    )
    return result


if __name__ == "__main__":
    report = preflight()
    print(json.dumps(report))
    raise SystemExit(0 if report["pass"] else 1)
