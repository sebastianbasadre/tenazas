"""Root-only, hash-pinned VM extension of the existing MCP and OAuth releases.

Preserves original modules via strict contextual patches, all runtime arguments,
TLS, OAuth configuration, tunnel and boot settings. Installs no NAS passwords.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

RELEASE = "jobs-chatgpt-v1"


def apply_text(original, patch):
    chunks, current = [], None
    for line in patch.splitlines(keepends=True):
        if line.startswith("@@"):
            current = []
            chunks.append(current)
        elif current is not None and line[:1] in {" ", "+", "-"}:
            current.append(line)
    if not chunks:
        raise ValueError("Empty patch")
    for chunk in chunks:
        before = "".join(line[1:] for line in chunk if line[0] in " -")
        after = "".join(line[1:] for line in chunk if line[0] in " +")
        if not before or original.count(before) != 1:
            raise ValueError("Installed source changed; review required")
        original = original.replace(before, after, 1)
    compile(original, "reviewed_mcp_extension.py", "exec")
    return original


def run(*arguments):
    return subprocess.check_output(arguments, stderr=subprocess.STDOUT, text=True)


def property(unit, name):
    return run("systemctl", "show", unit, "--property=" + name, "--value").strip()


def new_file(path, data, mode=0o640):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data if isinstance(data, bytes) else data.encode())
        stream.flush()
        os.fsync(stream.fileno())


def copy_python(source, destination, group):
    destination.mkdir(parents=True, mode=0o750)
    for file in source.rglob("*.py"):
        if file.is_symlink() or file.resolve() != file:
            raise ValueError("Unsafe source layout")
        target = destination / file.relative_to(source)
        new_file(target, file.read_bytes())
    for item in (destination, *destination.rglob("*")):
        os.chown(item, 0, group)
        item.chmod(0o750 if item.is_dir() else 0o640)


def install(bundle, expected):
    import grp
    import pwd

    if os.geteuid() != 0:
        raise ValueError("Root required")
    raw = bundle.read_bytes()
    if len(raw) > 2**20 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Bundle hash mismatch")
    payload = json.loads(raw)
    if set(payload) != {"gateway_patch", "proxy_patch", "files"} or set(payload["files"]) != {
        "mcp_applications_contract.py",
        "mcp_applications_client.py",
    }:
        raise ValueError("Bundle layout mismatch")
    gateway_base = Path(property("tenazas-mcp.service", "WorkingDirectory"))
    proxy_base = Path(property("tenazas-oauth-proxy.service", "WorkingDirectory"))
    if not str(gateway_base).startswith("/opt/tenazas-mcp/releases/") or not str(
        proxy_base
    ).startswith("/opt/tenazas-oauth-proxy/releases/"):
        raise ValueError("Unexpected installed releases")
    gateway_new = Path("/opt/tenazas-mcp/releases") / RELEASE
    proxy_new = Path("/opt/tenazas-oauth-proxy/releases") / RELEASE / "app"
    backup = Path("/var/backups/tenazas-applications-mcp") / RELEASE
    if any(p.exists() for p in (gateway_new, proxy_new.parent, backup)):
        raise ValueError("Refuse release overwrite")
    uid, gid = pwd.getpwnam("tenazas-mcp").pw_uid, grp.getgrnam("tenazas-mcp").gr_gid
    oauth_group = property("tenazas-oauth-proxy.service", "Group")
    oauth_gid = (
        grp.getgrnam(oauth_group).gr_gid
        if oauth_group
        else pwd.getpwnam(property("tenazas-oauth-proxy.service", "User")).pw_gid
    )
    backup.mkdir(parents=True, mode=0o700)
    for service in ("tenazas-mcp.service", "tenazas-oauth-proxy.service"):
        new_file(backup / (service + ".before"), run("systemctl", "cat", service), 0o600)
    copy_python(gateway_base, gateway_new, gid)
    copy_python(proxy_base, proxy_new, oauth_gid)
    gateway_path = gateway_new / "workspace/runtime/mcp_readonly_gateway.py"
    proxy_path = proxy_new / "workspace/runtime/mcp_oauth_proxy.py"
    gateway_path.write_text(apply_text(gateway_path.read_text(), payload["gateway_patch"]))
    proxy_path.write_text(apply_text(proxy_path.read_text(), payload["proxy_patch"]))
    for name, value in payload["files"].items():
        data = base64.b64decode(value, validate=True)
        compile(data, name, "exec")
        path = gateway_new / "workspace/runtime" / name
        new_file(path, data)
        os.chown(path, 0, gid)
    config = Path("/etc/tenazas-applications-mcp")
    config.mkdir(mode=0o750)
    os.chown(config, 0, gid)
    for name in ("nas-key", "known_hosts"):
        source = bundle.parent / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("SSH capability missing")
        new_file(config / name, source.read_bytes(), 0o400)
        os.chown(config / name, uid, gid)
    new_file(
        config / "config.json",
        json.dumps(
            {"key_file": str(config / "nas-key"), "known_hosts": str(config / "known_hosts")}
        ),
    )
    os.chown(config / "config.json", 0, gid)
    # Check the fixed SSH capability as the actual restricted service account.
    preflight = "from workspace.runtime.mcp_applications_client import ApplicationsClient; import json; r=ApplicationsClient('/etc/tenazas-applications-mcp/config.json').call('get_job_applications',{}); assert r['ok']; print('APPLICATIONS_SSH_OK')"
    subprocess.run(
        [
            "runuser",
            "-u",
            "tenazas-mcp",
            "--",
            "/usr/bin/env",
            "PYTHONPATH=" + str(gateway_new),
            "/usr/bin/python3",
            "-B",
            "-c",
            preflight,
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    effective = property("tenazas-mcp.service", "ExecStart")
    match = re.search(r"argv\[\]=(.*?);", effective)
    if not match:
        raise ValueError("Gateway argv unavailable")
    arguments = match[1].strip().split()
    if (
        arguments[:3] != ["/usr/bin/python3", "-m", "workspace.runtime.mcp_readonly_gateway"]
        or any(not re.fullmatch(r"[A-Za-z0-9_./:=+%-]+", a) for a in arguments)
        or "--applications-config" in arguments
    ):
        raise ValueError("Gateway argv changed")
    arguments += ["--applications-config", str(config / "config.json")]
    gateway_dropin = Path("/etc/systemd/system/tenazas-mcp.service.d/zzz-applications.conf")
    proxy_dropin = Path("/etc/systemd/system/tenazas-oauth-proxy.service.d/zzz-applications.conf")
    new_file(
        gateway_dropin,
        f"[Service]\nWorkingDirectory={gateway_new}\nEnvironment=PYTHONPATH={gateway_new}\nExecStart=\nExecStart={' '.join(arguments)}\n",
        0o644,
    )
    new_file(
        proxy_dropin,
        f"[Service]\nWorkingDirectory={proxy_new}\nEnvironment=PYTHONPATH={proxy_new}\n",
        0o644,
    )
    rollback = f"""#!/bin/sh
set -eu
systemctl stop tenazas-mcp.service
mv {gateway_dropin} {backup}/disabled-gateway.conf
mv {proxy_dropin} {backup}/disabled-proxy.conf
systemctl daemon-reload
systemctl restart tenazas-oauth-proxy.service
systemctl start tenazas-mcp.service
"""
    new_file(backup / "rollback.sh", rollback, 0o700)
    run("systemd-analyze", "verify", "tenazas-mcp.service", "tenazas-oauth-proxy.service")
    run("systemctl", "daemon-reload")
    try:
        # The old read-only proxy must never front the new mutation tools.
        run("systemctl", "stop", "tenazas-mcp.service")
        run("systemctl", "restart", "tenazas-oauth-proxy.service")
        run("systemctl", "start", "tenazas-mcp.service")
        run("systemctl", "is-active", "tenazas-mcp.service", "tenazas-oauth-proxy.service")
    except Exception:
        run("/bin/sh", str(backup / "rollback.sh"))
        raise
    print(
        json.dumps(
            {
                "status": "INSTALLED",
                "release": RELEASE,
                "write_scope_required": "write:applications",
                "gmail_credentials_transferred": False,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        install(args.bundle, args.sha256)
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "INSTALL_FAILED",
                    "type": type(error).__name__,
                    "reason": str(error) if type(error) is ValueError else "Details suppressed",
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
