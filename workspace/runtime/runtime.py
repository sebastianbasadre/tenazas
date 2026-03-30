#!/usr/bin/env python3
"""
Tenazas Runtime — Event-driven orchestration layer.

Runs every 5 minutes via cron. Idempotent.

Loop: update_state → detect_events → enqueue_tasks → run_tasks
      → append_memory → analyze_patterns → trigger_patterns
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Allow running as script or module
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime import executors

# ── Paths ─────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
EVENTS_FILE = BASE_DIR / "events.json"
TASKS_FILE = BASE_DIR / "tasks.json"
MEMORY_FILE = BASE_DIR / "memory_log.json"
PATTERNS_FILE = BASE_DIR / "patterns.json"
LOGS_DIR = BASE_DIR / "logs"

# ── Config ────────────────────────────────────────────────────────

ARR_CONTAINERS = {
    "gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
    "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd",
}
SKIP_CONTAINERS = {"openclaw", "watchtower"}
MAX_MEMORY_ENTRIES = 1000
PATTERN_ANALYSIS_INTERVAL = 3600  # seconds
PATTERN_MIN_OCCURRENCES = 3
PATTERN_LOOKBACK_DAYS = 7
TASK_COOLDOWN_SECONDS = 1200  # 20 min — don't re-restart same target
NMAP_INTERVAL_SECONDS = 1800  # 30 min — rate-limit nmap scans
GLOBAL_TIMEOUT_SECONDS = 240  # 4 min — kill if stuck
LOCK_FILE = BASE_DIR / "runtime.lock"
RUNTIME_LOG = LOGS_DIR / "runtime.log"

# ── JSON helpers ──────────────────────────────────────────────────


def _now():
    return datetime.now().astimezone()


def load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Lockfile ──────────────────────────────────────────────────────


def _acquire_lock() -> bool:
    """Acquire lockfile. Returns False if already running."""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            # Check if PID is still alive
            os.kill(pid, 0)
            return False  # Process still running
        except (ValueError, OSError):
            pass  # Stale lock — proceed
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock():
    """Release lockfile."""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


# ── Runtime Log ──────────────────────────────────────────────────


def _runtime_log(msg: str):
    """Append to runtime log with rotation at 500KB."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUNTIME_LOG, "a") as f:
        f.write(f"[{_now().isoformat()}] {msg}\n")
    try:
        if RUNTIME_LOG.stat().st_size > 500_000:
            lines = RUNTIME_LOG.read_text().splitlines()
            RUNTIME_LOG.write_text("\n".join(lines[len(lines) // 2:]) + "\n")
    except OSError:
        pass


# ── 1. State Engine ──────────────────────────────────────────────


def update_state() -> tuple:
    """Collect current system state. Returns (prev_state, current_state)."""
    prev = load_json(STATE_FILE, {})

    vpn = executors.check_vpn()
    if vpn.get("ok") and vpn.get("ip"):
        vpn["country"] = executors.check_vpn_country(vpn["ip"])

    ollama_notebook = executors.check_ollama_notebook()
    executors.write_ollama_status(ollama_notebook.get("ok", False))

    # Rate-limit nmap scans (every 30 min, not every 5 min)
    last_nmap = prev.get("_last_nmap", 0)
    if time.time() - last_nmap > NMAP_INTERVAL_SECONDS:
        network_devices = executors.check_network_devices()
        nmap_ts = time.time()
    else:
        network_devices = prev.get("network_devices", {"count": 0})
        nmap_ts = last_nmap

    curr = {
        "ts": _now().isoformat(),
        "containers": executors.check_containers(),
        "internet": executors.check_internet(),
        "vpn": vpn,
        "public_ip": executors.check_public_ip(),
        "ollama_notebook": ollama_notebook,
        "ollama_nas": executors.check_ollama_nas(),
        "network_devices": network_devices,
        "_last_nmap": nmap_ts,
    }

    save_json(STATE_FILE, curr)
    return prev, curr


# ── 2. Event Detection ───────────────────────────────────────────


def _container_map(state: dict) -> dict:
    """Build {name: up} from state containers list."""
    return {c["name"]: c["up"] for c in state.get("containers", [])}


def detect_events(prev: dict, curr: dict) -> list:
    """Compare prev vs curr state, return list of event dicts."""
    if not prev:
        return []  # First run = baseline

    ts = curr.get("ts", _now().isoformat())
    events = []

    # Container transitions
    prev_c = _container_map(prev)
    curr_c = _container_map(curr)

    for name, up in curr_c.items():
        if name.lower() in SKIP_CONTAINERS:
            continue
        was_up = prev_c.get(name)
        if was_up is True and not up:
            events.append({"type": "container_down", "container": name, "ts": ts})
        elif was_up is False and up:
            events.append({"type": "container_recovered", "container": name, "ts": ts})

    # Internet transitions
    prev_inet = prev.get("internet", {}).get("ok")
    curr_inet = curr.get("internet", {}).get("ok")
    if prev_inet is True and curr_inet is False:
        events.append({"type": "internet_lost", "ts": ts})
    elif prev_inet is False and curr_inet is True:
        events.append({"type": "internet_restored", "ts": ts})

    # VPN transitions
    prev_vpn = prev.get("vpn", {})
    curr_vpn = curr.get("vpn", {})
    if prev_vpn.get("ok") is True and curr_vpn.get("ok") is False:
        events.append({"type": "vpn_down", "ts": ts})
    elif curr_vpn.get("ok") and curr_vpn.get("country") == "AR":
        events.append({
            "type": "vpn_leak",
            "ip": curr_vpn.get("ip"), "country": "AR", "ts": ts,
        })
    elif (prev_vpn.get("ip") and curr_vpn.get("ip")
          and prev_vpn["ip"] != curr_vpn["ip"]):
        events.append({
            "type": "vpn_changed",
            "prev_ip": prev_vpn["ip"], "new_ip": curr_vpn["ip"], "ts": ts,
        })

    # Network device count change (threshold: >2 change)
    prev_dev = prev.get("network_devices", {}).get("count", 0)
    curr_dev = curr.get("network_devices", {}).get("count", 0)
    if abs(curr_dev - prev_dev) > 2:
        events.append({
            "type": "device_count_changed",
            "prev": prev_dev, "curr": curr_dev, "ts": ts,
        })

    # Ollama notebook transitions
    prev_oll = prev.get("ollama_notebook", {}).get("ok")
    curr_oll = curr.get("ollama_notebook", {}).get("ok")
    if prev_oll is True and curr_oll is False:
        events.append({"type": "ollama_notebook_down", "ts": ts})
    elif prev_oll is False and curr_oll is True:
        events.append({"type": "ollama_notebook_restored", "ts": ts})

    return events


# ── 3. Task Enqueueing ───────────────────────────────────────────


def _recent_actions(target: str) -> dict | None:
    """Check memory_log for a recent successful action on target within cooldown."""
    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        return None

    cutoff = _now() - timedelta(seconds=TASK_COOLDOWN_SECONDS)

    for entry in reversed(memory):
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError):
            continue
        if entry_dt < cutoff:
            break  # Entries are chronological, no need to keep looking
        for result in entry.get("results", []):
            if result.get("target") == target and result.get("ok"):
                return result
    return None


def enqueue_tasks(events: list) -> list:
    """Map events to executable tasks. Returns deduplicated task list with cooldown."""
    tasks = []
    seen_targets = set()

    # Sort: gluetun-related events first
    def priority(e):
        if e.get("container") == "gluetun" or e.get("type") in ("vpn_down", "vpn_leak"):
            return 0
        return 1

    for event in sorted(events, key=priority):
        etype = event["type"]

        if etype == "container_down":
            container = event["container"]
            if (container in ARR_CONTAINERS
                    and container not in seen_targets
                    and not _recent_actions(container)):
                tasks.append({
                    "action": "docker.restart", "target": container,
                    "reason": "container_down", "source": "event",
                })
                seen_targets.add(container)

        elif etype == "vpn_down" and "gluetun" not in seen_targets:
            if not _recent_actions("gluetun"):
                tasks.append({
                    "action": "docker.restart", "target": "gluetun",
                    "reason": "vpn_down", "source": "event",
                })
                seen_targets.add("gluetun")

        elif etype == "vpn_leak" and "gluetun" not in seen_targets:
            if not _recent_actions("gluetun"):
                tasks.append({
                    "action": "docker.restart", "target": "gluetun",
                    "reason": "vpn_leak_AR", "source": "event",
                })
                seen_targets.add("gluetun")

    return tasks


# ── 4. Task Execution ────────────────────────────────────────────


def run_tasks(tasks: list) -> list:
    """Execute all tasks and return results."""
    results = []
    for task in tasks:
        result = executors.execute_task(task)
        results.append(result)
    return results


# ── 5. Memory Log ────────────────────────────────────────────────


def append_memory(events: list, results: list):
    """Append run to memory_log.json (capped at MAX_MEMORY_ENTRIES)."""
    if not events and not results:
        return

    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        memory = []

    memory.append({
        "ts": _now().isoformat(),
        "events": events,
        "tasks_run": len(results),
        "results": results,
    })

    # Trim if over capacity
    if len(memory) > MAX_MEMORY_ENTRIES:
        memory = memory[-(MAX_MEMORY_ENTRIES // 2):]

    save_json(MEMORY_FILE, memory)


# ── 6. Pattern Engine ────────────────────────────────────────────


def analyze_patterns():
    """Detect recurring time-correlated events. Throttled to 1x/hour."""
    patterns_data = load_json(PATTERNS_FILE, {"patterns": [], "last_analysis": ""})
    last = patterns_data.get("last_analysis", "")

    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (_now() - last_dt).total_seconds() < PATTERN_ANALYSIS_INTERVAL:
                return  # Too soon
        except (ValueError, TypeError):
            pass

    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list) or len(memory) < PATTERN_MIN_OCCURRENCES:
        return

    cutoff = _now() - timedelta(days=PATTERN_LOOKBACK_DAYS)

    # Count (event_type, target, hour) occurrences
    counts = defaultdict(int)
    for entry in memory:
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError):
            continue
        if entry_dt < cutoff:
            continue
        for event in entry.get("events", []):
            key = (event.get("type", ""), event.get("container", ""), entry_dt.hour)
            counts[key] += 1

    # Build patterns from frequent occurrences
    new_patterns = []
    for (etype, target, hour), count in counts.items():
        if count >= PATTERN_MIN_OCCURRENCES:
            new_patterns.append({
                "event": etype,
                "target": target,
                "hour": hour,
                "occurrences": count,
                "last_seen": _now().isoformat(),
                "last_triggered": "",
            })

    # Merge with existing (keep last_triggered from old patterns)
    old_map = {(p["event"], p["target"], p["hour"]): p
               for p in patterns_data.get("patterns", [])}
    for p in new_patterns:
        key = (p["event"], p["target"], p["hour"])
        if key in old_map:
            p["last_triggered"] = old_map[key].get("last_triggered", "")

    patterns_data["patterns"] = new_patterns
    patterns_data["last_analysis"] = _now().isoformat()
    save_json(PATTERNS_FILE, patterns_data)


def trigger_patterns() -> list:
    """Check if current hour matches any pattern. Returns preemptive tasks."""
    patterns_data = load_json(PATTERNS_FILE, {"patterns": []})
    current_hour = _now().hour
    today = _now().date().isoformat()
    tasks = []

    for pattern in patterns_data.get("patterns", []):
        if pattern.get("hour") != current_hour:
            continue

        # Skip if already triggered today
        triggered = pattern.get("last_triggered", "")
        if triggered.startswith(today):
            continue

        etype = pattern.get("event", "")
        target = pattern.get("target", "")

        # Only generate preemptive restart tasks for container_down patterns
        if (etype == "container_down" and target in ARR_CONTAINERS
                and not _recent_actions(target)):
            tasks.append({
                "action": "docker.restart", "target": target,
                "reason": f"pattern:{etype}@{current_hour}h",
                "source": "pattern",
            })
            pattern["last_triggered"] = _now().isoformat()

    if tasks:
        save_json(PATTERNS_FILE, patterns_data)

    return tasks


# ── Main Loop ─────────────────────────────────────────────────────


def _build_notification(events: list, results: list, curr: dict) -> str | None:
    """Build Telegram message if there's anything worth reporting."""
    lines = []
    for r in results:
        ok = r.get("ok", False)
        icon = "ok" if ok else "FAIL"
        lines.append(f"[{icon}] {r.get('action')} {r.get('target')} ({r.get('reason','')})")

    # Notify on ollama down even without restart actions
    ollama = curr.get("ollama_notebook", {})
    if not ollama.get("ok"):
        lines.append("[info] Notebook Ollama offline — Cloud/NAS CPU mode")

    if not lines:
        return None

    ts = datetime.fromisoformat(curr["ts"]).strftime("%H:%M")
    header = f"Runtime — {ts} ART"
    return header + "\n" + "\n".join(lines)


def main():
    start = time.time()

    # Lockfile: prevent concurrent runs
    if not _acquire_lock():
        return

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _runtime_log("cycle start")

        prev, curr = update_state()
        events = detect_events(prev, curr)
        save_json(EVENTS_FILE, {"ts": curr["ts"], "events": events})

        tasks = enqueue_tasks(events)
        pattern_tasks = trigger_patterns()
        all_tasks = tasks + pattern_tasks
        save_json(TASKS_FILE, {"ts": curr["ts"], "tasks": all_tasks})

        results = run_tasks(all_tasks)
        append_memory(events, results)
        analyze_patterns()

        msg = _build_notification(events, results, curr)
        if msg:
            executors.notify_telegram(msg)

        elapsed = round(time.time() - start, 1)
        _runtime_log(f"cycle done events={len(events)} tasks={len(all_tasks)} elapsed={elapsed}s")

        # Watchdog: warn if approaching timeout
        if elapsed > GLOBAL_TIMEOUT_SECONDS:
            _runtime_log(f"WARNING: cycle exceeded {GLOBAL_TIMEOUT_SECONDS}s timeout")

    except Exception as e:
        _runtime_log(f"ERROR: {e}")
        raise

    finally:
        _release_lock()


if __name__ == "__main__":
    main()
