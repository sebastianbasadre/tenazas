"""Staging-only harness: copied NAS entrypoints, synthetic probes, no real tasks.

The host runner mounts /private-state and /mcp-cache from an isolated test area.
This script deliberately does not import or launch production services.
"""

import ast
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from tools.staging_private_json import save_json

UID = GID = 1026  # Explicit container numeric identity; NOT NAS tenazas (1035).
STATE = Path("/private-state/state.json")
CACHE = Path("/mcp-cache")
RUNTIME = Path("/home/tenazas/runtime")


def prepare():
    assert (os.geteuid(), os.getegid()) == (UID, GID)
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    originals = Path("/entrypoints")
    for name in ("runtime.py", "runtime_utils.py", "worker_supervisor.py", "service_supervisor.py", "process_lock.py"):
        shutil.copyfile(originals / name, RUNTIME / name)
    (RUNTIME / "workers").mkdir(mode=0o700)
    shutil.copyfile(originals / "runtime_worker.py", RUNTIME / "workers/runtime_worker.py")
    (RUNTIME / "__init__.py").write_text("")
    source = (RUNTIME / "runtime.py").read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "save_json")
    lines = source.splitlines(keepends=True)
    # Only the COPIED function changes. update_state/main/worker entrypoints stay real.
    lines[function.lineno - 1:function.end_lineno] = [
        "def save_json(path, data):\n",
        "    from tools.staging_private_json import save_json as private_write\n",
        "    return private_write(path, data, uid=1026, gid=1026)\n",
    ]
    source = "".join(lines)
    before_main = source.rfind('if __name__ == "__main__":')
    overrides = '''
# STAGING ONLY: fixed private path, deterministic probes, no real actions.
STATE_FILE = Path("/private-state/state.json")
EVENTS_FILE = Path("/private-state/events.json")
TASKS_FILE = Path("/private-state/tasks.json")
detect_events = lambda *a: []
enqueue_tasks = lambda *a: []
run_tasks = lambda *a: []
append_memory = lambda *a: None
analyze_patterns = lambda *a: None
trigger_patterns = lambda *a: []
_network_scan = lambda *a: None
_check_arr_port_bindings = lambda *a: []
_build_notification = lambda *a: None
_watchdog_thread = lambda: None
'''
    if os.environ.get("STAGING_CANONICAL_PRIVATE_SUBDIR") == "1":
        overrides = overrides.replace('STATE_FILE = Path("/private-state/state.json")',
                                      'STATE_FILE = BASE_DIR / "private-state" / "state.json"')
        Path("/home/tenazas/sitecustomize.py").write_text('''
import os, sys
def reject_legacy(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.abspath(os.fsdecode(args[0])) == "/home/tenazas/runtime/state.json":
            raise RuntimeError("Legacy state path forbidden in staging")
sys.addaudithook(reject_legacy)
''')
    (RUNTIME / "runtime.py").write_text(source[:before_main] + overrides + source[before_main:])
    supervisor = (RUNTIME / "worker_supervisor.py").read_text()
    tree = ast.parse(supervisor)
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "WORKERS" for t in n.targets))
    lines = supervisor.splitlines(keepends=True)
    lines[assignment.lineno - 1:assignment.end_lineno] = [
        'WORKERS = [("runtime_worker", "workers/runtime_worker.py", 60)]\n'
    ]
    (RUNTIME / "worker_supervisor.py").write_text("".join(lines))
    (RUNTIME / "executors.py").write_text('''
def check_vpn(): return {"ok": True}
def check_vpn_country(ip): return "staging"
def check_ollama_notebook(): return {"ok": True}
def write_ollama_status(ok): pass
def check_containers(): return [{"name": "staging-openclaw", "up": True}]
def check_internet(): return {"ok": True, "Authorization": "STAGING-PRIVATE-CREDENTIAL"}
def check_public_ip(): return "192.0.2.1"
def check_ollama_nas(): return {"ok": True}
def notify_telegram(*args): raise AssertionError("No real notification allowed")
''')
    (RUNTIME / "freeze_detector.py").write_text('''
from types import SimpleNamespace
def start(): pass
def stop(): pass
def heartbeat(*args): pass
def get_detector(): return SimpleNamespace(freeze_count=0, get_step_durations=lambda: {})
''')
    (RUNTIME / "metrics_exporter.py").write_text('''
def record_run(*args): pass
def record_failure(*args): pass
def record_step(*args): pass
def calculate_health(*args, **kwargs): return 100
def set_health_score(*args): pass
''')
    # process_lock is imported as a top-level module by the captured supervisor.
    shutil.copyfile(originals / "process_lock.py", Path("/home/tenazas/process_lock.py"))
    print(json.dumps({"prepared": True, "source_sha256": {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals.iterdir()}}))


def service_cycle():
    sys.path[:0] = [str(RUNTIME), "/home/tenazas"]
    import service_supervisor as service
    cycle = next(s for s in service.services() if s.name == "runtime_cycle")
    cycle.logger = logging.getLogger("staging.runtime_cycle")
    service.start(cycle)
    assert cycle.process.wait(timeout=60) == 0
    state = json.loads((RUNTIME / "supervisor_state.json").read_text())
    assert len(state["workers"]) == 1 and state["workers"][0]["ok"]
    print(json.dumps({"route": "service_supervisor.start -> worker_supervisor.main -> runtime_worker.run",
                      "ok": True, "worker_result": state["workers"][0]["result"]}))


def verify_cycle(*, rewrite=True):
    from workspace.runtime import mcp_cache_producer as producer
    from workspace.runtime import mcp_cache_policy
    info = STATE.stat()
    assert (info.st_mode & 0o7777, info.st_uid, info.st_gid) == (0o600, UID, GID)
    data = json.loads(STATE.read_text())
    assert data["containers"][0]["name"] == "staging-openclaw"
    # Reading and a second write through the candidate verify the same UID's
    # application access; this is NOT a live production OpenClaw smoke test.
    if rewrite:
        before = info.st_ino
        save_json(STATE, data, uid=UID, gid=GID)
        assert STATE.stat().st_ino != before
    producer._write = lambda path, value: save_json(path, value, uid=UID, gid=GID)
    producer.export_cache(data, secret_values=["STAGING-PRIVATE-CREDENTIAL"], destination=CACHE)
    mcp_cache_policy.validate_cache(CACHE)
    metadata = []
    for path in [STATE, CACHE, CACHE / "incidents", *sorted(CACHE.rglob("*.json"))]:
        stat = path.stat()
        assert (stat.st_uid, stat.st_gid) == (UID, GID)
        if path.is_file():
            json.loads(path.read_text())
            if path.is_relative_to(CACHE):
                assert "STAGING-PRIVATE-CREDENTIAL" not in path.read_text()
        metadata.append({"path": str(path), "mode": oct(stat.st_mode & 0o7777),
                         "uid": stat.st_uid, "gid": stat.st_gid, "inode": stat.st_ino})
    print(json.dumps({"valid_json": True, "same_uid_read_write": rewrite,
                      "zero_synthetic_secret_matches": True, "metadata": metadata}))


if __name__ == "__main__":
    {"prepare": prepare, "service-cycle": service_cycle, "verify-cycle": verify_cycle,
     "export-only": lambda: verify_cycle(rewrite=False)}[sys.argv[1]]()
