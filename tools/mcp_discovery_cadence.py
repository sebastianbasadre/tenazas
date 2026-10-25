"""Prepare and run pinned NAS discovery cadence; installation never starts a scan.

Linux/NAS only for install/run. Pure transformations are testable elsewhere.
The existing administrative launcher retains its release and Watchtower gates.
"""

import argparse
import ast
import hashlib
import json
import os
import runpy
import stat
import subprocess
import sys
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = Path("/volume1/tenazas-mcp-discovery-v2")
LEGACY = Path("/volume1/docker/openclaw/workspace/runtime")
ROOT = BASE / "cadence"
CONFIG = ROOT / "config.json"
PYTHON = "/usr/local/bin/python3.13"
IMAGE = "sha256:bd6795ea0ff48bd365a79e405474cb27d3265c24bdcf5d0a95d3d46282fec9be"
MANIFEST = "af36ef7d712d94c07d9c527351b22ca1252669c8568a8522387f1f7c09f3d4fa"
TEMPLATE_ID = "refresh-3d7fd50b35c145a6868bef3ef01441ea"
UNIT = "tenazas-mcp-discovery-refresh"
GATE_NAME = ".mcp-discovery-cadence.lock"


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_new(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def logged_command(args, path):
    """Persist bounded launcher diagnostics even when the terminal disconnects."""
    save_new(path, b"")
    with path.open("ab") as stream:
        try:
            return subprocess.run(args, check=True, stdout=stream, stderr=subprocess.STDOUT)
        finally:
            stream.flush()
            os.fsync(stream.fileno())
            tail = path.read_bytes()[-8192:].decode(errors="replace")
            if tail:
                print(tail, end="" if tail.endswith("\n") else "\n", flush=True)


def protected_read(path):
    """Read root-owned configuration/code; reject symlinks in every ancestor."""
    path = Path(path)
    require(path.is_absolute(), "Absolute path required")
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022,
            "Untrusted parent: " + str(parent),
        )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022
            and info.st_size <= 2**20,
            "Untrusted file: " + str(path),
        )
        raw = stream.read(2**20 + 1)
        after = os.fstat(stream.fileno())

        def token(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        require(len(raw) <= 2**20 and token(info) == token(after), "File changed")
        return raw


def make_guard(raw):
    """Wrap the whole runtime network step, including its separate nmap call."""
    text = raw.decode()
    tree = ast.parse(text)
    functions = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_network_scan"
    ]
    require(len(functions) == 1, "Expected one runtime network function")
    node = functions[0]
    require(not node.decorator_list and len(node.args.args) == 1, "Unexpected network function")
    require("_network_scan_without_cadence_guard" not in text, "Guard already installed")
    lines = text.splitlines(keepends=True)
    index = node.lineno - 1
    require(
        lines[index].startswith("def _network_scan(curr: dict):"), "Unexpected network signature"
    )
    lines[index] = lines[index].replace(
        "def _network_scan(", "def _network_scan_without_cadence_guard(", 1
    )
    wrapper = """def _network_scan(curr: dict):
    # Shared with the staging cadence runner; never fabricate scan observations.
    import fcntl
    gate = Path(__file__).with_name(".mcp-discovery-cadence.lock")
    descriptor = os.open(gate, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        return _network_scan_without_cadence_guard(curr)
    finally:
        os.close(descriptor)


"""
    lines.insert(index, wrapper)
    updated = "".join(lines).encode()
    compile(updated, "runtime.py", "exec")
    return updated


def make_launcher(raw, directory):
    text = raw.decode()
    replacements = {
        f'AUTH = CONTROL / "{TEMPLATE_ID}-authorization.json"': "AUTH = Path("
        + repr(str(directory / "authorization.json"))
        + ")",
        f'CONTROL / "{TEMPLATE_ID}-execution.claim"': "Path("
        + repr(str(directory / "execution.claim"))
        + ")",
    }
    for old, new in replacements.items():
        require(text.count(old) == 1, "Unexpected refresh launcher structure")
        text = text.replace(old, new, 1)
    require('CONTROL / "275A182-administrative.lock"' in text, "Shared administrative lock missing")
    require('"com.centurylinklabs.watchtower.enable=false"' in text, "Exclusion label missing")
    compile(text, "launcher.py", "exec")
    return text.encode()


def unit_files(interval):
    require(type(interval) is int and 120 <= interval <= 3600, "Interval must be 120–3600 seconds")
    service = f"""[Unit]
Description=Tenazas pinned staging discovery refresh

[Service]
Type=oneshot
User=root
WorkingDirectory={ROOT}
ExecStart={PYTHON} -ISB {ROOT}/runner.py run
UMask=0077
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
TimeoutStartSec=0
"""
    timer = f"""[Unit]
Description=Tenazas staging discovery cadence

[Timer]
OnActiveSec=30s
OnUnitInactiveSec={interval}s
AccuracySec=1s
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return service.encode(), timer.encode()


def root_only():
    require(os.name == "posix" and os.geteuid() == 0, "Run as root on the NAS")
    os.umask(0o077)


@contextmanager
def locked(path):
    import fcntl

    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe coordination lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        linked = Path(path).lstat()
        require(
            (info.st_dev, info.st_ino) == (linked.st_dev, linked.st_ino),
            "Coordination inode changed",
        )
        yield
    finally:
        os.close(fd)


def active_scans():
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            names = {
                os.path.basename(os.fsdecode(arg))
                for arg in (entry / "cmdline").read_bytes().split(b"\0")
                if arg
            }
            if names & {"runtime.py", "device_discovery.py", "nmap"}:
                result.append(int(entry.name))
        except FileNotFoundError:
            pass
    return result


def install(interval, accept):
    root_only()
    require(accept, "Recurring image-review risk acceptance required")
    require(not ROOT.exists(), "Cadence already prepared; do not overwrite")
    runtime = LEGACY / "runtime.py"
    original = runtime.read_bytes()
    updated = make_guard(original)
    template = protected_read(BASE / "admin" / (TEMPLATE_ID + ".py"))
    manifest_raw = protected_read(BASE / "releases/275A180/release-manifest.json")
    require(sha(manifest_raw) == MANIFEST, "Release manifest mismatch")
    manifest = json.loads(manifest_raw)
    reader = BASE / "releases/275A180/writer/network_discovery_result.py"
    reader_raw = protected_read(reader)
    require(
        manifest["writer_files"]["network_discovery_result.py"] == sha(reader_raw),
        "Reader hash mismatch",
    )
    service, timer = unit_files(interval)
    make_launcher(template, ROOT / "test-only")  # Validate the template before mutations.
    units = Path("/etc/systemd/system")
    require(
        not any((units / (UNIT + suffix)).exists() for suffix in (".service", ".timer")),
        "Cadence units already exist",
    )
    info = runtime.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe runtime source")
    require(not active_scans(), "Runtime/scanner active; wait for it to finish before installation")
    ROOT.mkdir(mode=0o700)
    save_new(ROOT / "runtime.before.py", original)
    save_new(ROOT / "template.py", template)
    save_new(ROOT / "runner.py", Path(__file__).read_bytes())
    save_new(ROOT / (UNIT + ".service"), service)
    save_new(ROOT / (UNIT + ".timer"), timer)
    gate = LEGACY / GATE_NAME
    save_new(gate, b"")
    os.chown(gate, 1026, 1026)
    config = {
        "schema": 1,
        "interval_seconds": interval,
        "image_review_limitations_accepted_for_recurring_pinned_runs": True,
        "image": IMAGE,
        "manifest_sha256": MANIFEST,
        "template_sha256": sha(template),
        "runtime_sha256": sha(updated),
        "runner_sha256": sha((ROOT / "runner.py").read_bytes()),
        "reader_sha256": sha(reader_raw),
        "accepted_at": datetime.now(UTC).isoformat(),
    }
    save_new(CONFIG, json_bytes(config))
    # Replace one source file; keep its original ownership/mode and a private backup.
    temporary = runtime.with_name(".runtime-cadence-" + uuid.uuid4().hex)
    save_new(temporary, updated, stat.S_IMODE(info.st_mode))
    os.chown(temporary, info.st_uid, info.st_gid)
    require(runtime.read_bytes() == original, "Runtime changed during installation")
    os.replace(temporary, runtime)
    print(
        json.dumps(
            {
                "status": "PREPARED_NOT_SCHEDULED",
                "directory": str(ROOT),
                "interval_seconds": interval,
                "task_12": "leave enabled",
                "scan_executed": False,
            },
            indent=2,
        )
    )


def validate_publication(module, before, started):
    data = BASE / "staging/runtime"
    result = data / "network_discovery_result.json"
    inventory = data / "device_inventory.json"
    signal = module["read_result"](result)
    require(
        signal["status"] == "success" and signal["attempt_id"] != before["attempt_id"],
        "No new successful publication",
    )
    observed = datetime.fromisoformat(signal["last_success"]["observed_at"].replace("Z", "+00:00"))
    require(observed >= started, "Observation predates cycle")
    store = module["DiscoveryStore"](inventory, result)
    for key in ("current", "last_success"):
        require(signal[key] is not None, "Missing generation reference")
        store.verify_generation(signal[key])
    records = store.records()
    for path, mode in ((result, 0o644), (inventory, 0o600)):
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (info.st_uid, info.st_gid) == (1026, 1026)
            and stat.S_IMODE(info.st_mode) == mode,
            "Publication permissions mismatch",
        )
    return {
        "status": "PASS",
        "attempt_id": signal["attempt_id"],
        "observed_at": signal["last_success"]["observed_at"],
        "inventory_records": len(records),
    }


def run_cycle():
    root_only()
    require(
        not (ROOT / "blocked.json").exists(), "Cadence latched after a failure; review required"
    )
    config = json.loads(protected_read(CONFIG))
    require(
        config["image_review_limitations_accepted_for_recurring_pinned_runs"] is True
        and config["image"] == IMAGE
        and config["manifest_sha256"] == MANIFEST,
        "Recurring approval mismatch",
    )
    require(sha(protected_read(Path(__file__))) == config["runner_sha256"], "Runner changed")
    template = protected_read(ROOT / "template.py")
    require(sha(template) == config["template_sha256"], "Launcher changed")
    require(
        sha((LEGACY / "runtime.py").read_bytes()) == config["runtime_sha256"],
        "Runtime guard changed",
    )
    reader = BASE / "releases/275A180/writer/network_discovery_result.py"
    require(sha(protected_read(reader)) == config["reader_sha256"], "Reader changed")
    with ExitStack() as stack:
        try:
            stack.enter_context(locked(LEGACY / GATE_NAME))
            stack.enter_context(locked(LEGACY / "network_discovery_result.json.lock"))
        except BlockingIOError:
            print("SKIP_BUSY: legacy scan or another cycle holds coordination lock")
            return
        if active_scans():
            print("SKIP_BUSY: runtime/scanner active; observation unchanged")
            return
        module = runpy.run_path(str(reader))
        before = module["read_result"](BASE / "staging/runtime/network_discovery_result.json")
        module["DiscoveryStore"](
            BASE / "staging/runtime/device_inventory.json",
            BASE / "staging/runtime/network_discovery_result.json",
        ).records()
        directory = ROOT / ("run-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        started = datetime.now(UTC)
        auth = {
            "approved": True,
            "image_id": IMAGE,
            "release_manifest_sha256": MANIFEST,
            "mode": "manual_once",
            "subnet": "192.168.2.0/24",
            "expires_at": (started + timedelta(minutes=15)).isoformat(),
            "recurring_approval_config_sha256": sha(protected_read(CONFIG)),
        }
        for key in (
            "image_runtime_dependencies_verified",
            "image_no_secrets_reviewed",
            "writer_uid_gid_access_verified",
            "source_parent_access_verified",
            "scan_scope_authorized",
            "other_dispatchers_excluded",
            "watchtower_exclusion_window_authorized",
        ):
            auth[key] = True
        authpath = directory / "authorization.json"
        save_new(authpath, json_bytes(auth))
        launcher = directory / "launcher.py"
        save_new(launcher, make_launcher(template, directory))
        save_new(directory / "result-before.json", json_bytes(before))
        stage = "launcher_preflight"
        try:
            logged_command([PYTHON, "-ISB", str(launcher), "--check"], directory / "check.log")
            stage = "writer_execution"
            logged_command([PYTHON, "-ISB", str(launcher), "--run"], directory / "run.log")
            stage = "container_removal"
            remaining = subprocess.run(
                [
                    "/usr/local/bin/docker",
                    "ps",
                    "-aq",
                    "--filter",
                    "name=^/tenazas-mcp-discovery-staging-writer$",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            require(not remaining.stdout.strip(), "Staging writer removal not confirmed")
            stage = "publication_validation"
            report = validate_publication(module, before, started)
            save_new(directory / "validation.json", json_bytes(report))
            print(json.dumps(report, sort_keys=True), flush=True)
        except BaseException as error:
            failure = {
                "status": "REVIEW_REQUIRED",
                "run": directory.name,
                "stage": stage,
                "error_type": type(error).__name__,
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            if isinstance(error, subprocess.CalledProcessError):
                failure["command_exit_code"] = error.returncode
            save_new(
                ROOT / "blocked.json",
                json_bytes(failure),
            )
            save_new(directory / "failure.json", json_bytes(failure))
            raise
        finally:
            os.rename(authpath, directory / "authorization-retired.json")


def schedule():
    """Install/start only the timer, after a successful unscheduled proof cycle."""
    root_only()
    config = json.loads(protected_read(CONFIG))
    require(not (ROOT / "blocked.json").exists(), "Cadence is blocked; review required")
    require(
        sha(protected_read(Path(__file__))) == config["runner_sha256"],
        "Runner changed",
    )
    require(
        any((entry / "validation.json").is_file() for entry in ROOT.glob("run-*")),
        "Complete one successful unscheduled cycle before scheduling",
    )
    expected = unit_files(config["interval_seconds"])
    for suffix, raw in zip((".service", ".timer"), expected, strict=True):
        candidate = ROOT / (UNIT + suffix)
        require(protected_read(candidate) == raw, "Unit candidate changed")
        destination = Path("/etc/systemd/system") / candidate.name
        require(
            not Path(str(destination) + ".d").exists(),
            "Unexpected cadence unit drop-ins require review",
        )
        if destination.exists():
            require(protected_read(destination) == raw, "Installed cadence unit differs")
        else:
            save_new(destination, raw, 0o644)
    subprocess.run(["/bin/systemctl", "daemon-reload"], check=True)
    subprocess.run(["/bin/systemctl", "start", UNIT + ".timer"], check=True)
    print("Cadence timer started. Boot enablement has not been changed.")
    print("To stop future scans: sudo systemctl stop " + UNIT + ".timer")
    print("Let any active refresh service finish; do not terminate its scan.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--interval-seconds", type=int, required=True)
    prepare.add_argument("--accept-reviewed-image-limitations", action="store_true")
    sub.add_parser("run")
    sub.add_parser("schedule")
    args = parser.parse_args()
    if args.command == "prepare":
        install(args.interval_seconds, args.accept_reviewed_image_limitations)
    elif args.command == "run":
        run_cycle()
    else:
        schedule()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("BLOCKED:", type(error).__name__, str(error), file=sys.stderr)
        sys.exit(1)
